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
from municipality.models import (
    TopicSubject,
    TopicSubjectQualityReport,
    TopicSubjectRun,
    TopicSubjectV3Event,
    TopicSubjectV3RowQuality,
    TopicSubjectV3Run,
)
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
PROVENANCE_V3 = "topic_subject_research_v3_dicta_contextual_event"
V3_ACTION_CONFIDENCE_THRESHOLD = 0.75
DEFAULT_DICTA_SMALL_MODEL = "dicta-il/DictaLM-3.0-1.7B-Thinking:latest"

TOPIC_SUBJECT_HEAVY_MODEL_STAGES = {
    "topic_subject_legacy_extraction",
    "topic_subject_v3_contextual_event_normalization",
    "topic_subject_v3_action_subject_extraction",
    "topic_subject_v3_event_judge",
}
TOPIC_SUBJECT_SMALL_MODEL_STAGES = {
    "topic_subject_json_repair",
    "topic_subject_v3_json_repair",
    "topic_subject_v3_quote_repair",
    # Short and bounded by supplied evidence; final V3 judging still uses the heavy model.
    "topic_subject_v3_evidence_entailment",
}

ACTION_ONTOLOGY_V3 = (
    {
        "label_he": "שאילתה",
        "description_en": "A council inquiry/question submitted or raised for municipal response.",
    },
    {
        "label_he": "מענה לשאילתה",
        "description_en": "A response or answer to a council inquiry.",
    },
    {
        "label_he": "בקשה",
        "description_en": "A request for discussion, approval, information, authorization, or action before a municipal body.",
    },
    {
        "label_he": "הצעה לסדר יום",
        "description_en": "A proposal to place or discuss a matter on the agenda.",
    },
    {
        "label_he": "המלצה",
        "description_en": "A recommendation by a committee, official, or professional body, without necessarily being approved.",
    },
    {
        "label_he": "דיווח",
        "description_en": "A report, review, update, presentation, or informational response that is not a formal decision.",
    },
    {
        "label_he": "הנחיה",
        "description_en": "An instruction, follow-up task, directive, or operational instruction.",
    },
    {
        "label_he": "הפניה לוועדה",
        "description_en": "A referral or transfer of a matter to a committee or other municipal body.",
    },
    {
        "label_he": "אישור",
        "description_en": "A municipal body approves an action, agreement, allocation, appointment, protocol, participation, or similar matter.",
    },
    {
        "label_he": "דחייה",
        "description_en": "A municipal body rejects or declines a request, proposal, or decision candidate.",
    },
    {
        "label_he": "הסרה מסדר היום",
        "description_en": "A matter is removed from the agenda or not discussed as scheduled.",
    },
    {
        "label_he": "אחר",
        "description_en": "Use when Dicta is not highly confident that one of the controlled labels fits.",
    },
)
ACTION_ONTOLOGY_LABELS_V3 = tuple(row["label_he"] for row in ACTION_ONTOLOGY_V3)

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

COORDINATION_ACTION_CUES = (
    "לתאם",
    "תיאום",
    "יש לתאם",
    "יקבע מפגש",
    "לקבוע מפגש",
    "פגישה",
    "מפגש",
)

EXECUTION_ACTION_CUES = (
    "לפעול",
    "לטפל",
    "טיפול",
    "לבצע",
    "ביצוע",
    "לשלוח",
    "להחליף",
    "להוסיף",
    "הסרה",
    "אכיפה",
)

EXERCISE_ACTION_CUES = (
    "תרגיל",
    "תרגול",
    "יתרגלו",
)

NOTIFICATION_ACTION_CUES = (
    "הודעה בדבר",
    "הודעה על",
    "מודיע",
    "מודיעה",
)

PROTOCOL_APPROVAL_CUES = (
    "אישור פרוטוקול",
    "מאשרים פה אחד את הפרוטוקול",
    "מאשרים ברוב קולות את הפרוטוקול",
    "מאשרים את הפרוטוקול",
    "אישרו את הפרוטוקול",
    "אושר הפרוטוקול",
)

UPDATE_ACTION_CUES = (
    "שינוי",
    "עדכון",
    "מעודכן",
    "לעדכן",
)

RECOMMENDATION_ACTION_CUES = (
    "הוועדה ממליצה",
    "הועדה ממליצה",
    "ממליצה למועצת העיר",
    "ממליץ למועצת העיר",
    "ממליצים למועצת העיר",
    "ממליצה לשנות",
    "ממליץ לשנות",
)

REPORT_ACTION_CUES = (
    "מדווח",
    "מדווחת",
    "מדווחים",
    "דיווח",
    "סקירה",
    "סוקר",
    "סוקרת",
    "מציג",
    "מציגה",
    "מסכם",
    "מסכמת",
    "להלן התייחסות",
    "נמסר לוועדה",
    "נמסר לועדה",
)

RESPONSE_TO_INQUIRY_ACTION_CUES = (
    "מענה לשאילתה",
    "מענה לשאילתא",
    "במענה לשאילתא",
    "במענה לשאילת א",
    "במענה לשאילתה",
    "במענה לשאילתה של",
    "במענה לשאילתא של",
    "במענה לשאילת א של",
    "להלן התייחסותי",
    "להלן התייחסותי לשאילתה",
    "להלן התייחסותי לשאילתא",
    "הוקראה תשובת",
    "תשובת ראש העיר",
    "השאלה והתשובה מצורפות",
)

DIRECTIVE_ACTION_CUES = (
    "יש לתאם",
    "יש לעדכן",
    "יש לפעול",
    "להעביר לטיפול",
    "הועבר לטיפול",
    "עוברת לדיון",
    "יועבר לדיון",
    "לפעול בהתאם",
    "לפעול מידית",
    "לקדם את",
)

AGENDA_REMOVAL_CUES = (
    "יורדת מסדר היום",
    "ירדה מסדר היום",
    "הוסרה מסדר היום",
    "להסיר מסדר היום",
)

COMMITTEE_REFERRAL_CUES = (
    "עוברת לדיון בוועדה",
    "עוברת לדיון בועדה",
    "עוברת לדיון בוועדת",
    "עוברת לדיון בועדת",
    "מועברת לדיון בוועדה",
    "מועברת לדיון בועדה",
    "מועברת לוועדה",
    "מועברת לועדה",
    "להעביר לוועדה",
    "להעביר לועדה",
)

COMMITTEE_DECISION_APPROVAL_CUES = (
    "מאשרת את החלטת",
    "מאשר את החלטת",
    "אישור החלטת ועדה",
    "אישור החלטה של ועדה",
    "מאשרת את החלטה",
    "מאשר את החלטה",
)

AGENDA_PROPOSAL_CUES = (
    "הצעה לסדר יום",
    "הצעה לסדר",
    "הצעה סדר יום",
    "להעלות את הנושא לסדר יום",
    "לעלות את הנושא לסדר יום",
    "אבקש להביא את ההצעה לסדר יום",
)

AGENDA_REQUEST_TITLE_CUES = (
    "בקשתו",
    "בקשתה",
    "בקשתם",
    "בקשתן",
    "בקשת חבר",
    "בקשת חברת",
    "בקשת חברי",
    "לבקשתו",
    "לבקשתה",
    "לבקשתם",
)

REQUEST_ACTION_CUES = REQUEST_ONLY_CUES + AGENDA_REQUEST_TITLE_CUES

NAKED_CONTEXT_ROOTS = {"הגדרה", "פעולה", "תרגיל", "רישום", "תיאום", "ביצוע", "עדכון", "מסגרת מקצועית"}
VOTE_METADATA_ROOTS = {"נמנע", "הצבעה"}

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

APPROVAL_DECISION_QUOTE_CUES = (
    "חברי המועצה מאשרים פה אחד את",
    "חברי המועצה מאשרים ברוב קולות את",
    "חברי המועצה מאשרים את",
    "חברי המועצה מאשרים",
    "המועצה מאשרת",
    "המועצה אישרה",
    "מאשרים פה אחד את",
    "מאשרים ברוב קולות את",
    "מאשרים את",
    "מאשרת את החלטת",
    "מאשר את החלטת",
    "הוחלט לאשר",
    "ההצעה התקבלה",
    "התקבלה",
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
    small_model_name: str = DEFAULT_DICTA_SMALL_MODEL
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    timeout_seconds: float = 180.0
    max_text_chars: int = 3500
    offset: int = 0
    limit: int | None = None
    write: bool = False
    output_dir: Path | None = None
    pipeline_version: str = "legacy"
    action_confidence_threshold: float = V3_ACTION_CONFIDENCE_THRESHOLD
    max_context_rows: int = 5


def topic_subject_model_for_stage(*, stage: str, config: TopicSubjectResearchConfig) -> str:
    if stage in TOPIC_SUBJECT_SMALL_MODEL_STAGES:
        return config.small_model_name
    # Unknown or semantic stages use the heavy model by default to avoid silent accuracy loss.
    return config.model_name


def topic_subject_thinking_enabled_for_model(model_name: str) -> bool:
    normalized = model_name.lower()
    return "thinking" in normalized or "think" in normalized


def topic_subject_system_prompt(content: str, *, think: bool) -> str:
    prompt = content.removeprefix("/no_think\n")
    if think:
        return prompt
    return f"/no_think\n{prompt}"


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
    corrected_text: str
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
    action_root_by_dicta: str = ""
    action_child_by_dicta: str = ""
    subject_matter_by_dicta: str = ""
    action_details_by_dicta: str = ""
    event_id: str = ""
    event_topic: str = ""
    row_role: str = ""
    anchor_status: str = ""
    linked_event_id: str = ""
    link_confidence: float = 0.0
    link_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TopicSubjectEventBlock:
    block_id: str
    kind: str
    artifacts: list[TopicDecisionArtifact]
    anchor_artifact: TopicDecisionArtifact | None
    local_payload_by_artifact_id: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class TopicSubjectV3EventContext:
    context_id: str
    target_artifact: TopicDecisionArtifact
    rows: list[TopicDecisionArtifact]


@dataclass(slots=True)
class TopicSubjectV3RowQualityData:
    artifact: TopicDecisionArtifact
    event_id: str
    row_role: str
    event_role: str
    topic_relevance: str
    action_type_by_dicta: str
    action_subtype_by_dicta: str
    other_action_type_by_dicta: str
    matter_by_dicta: str
    action_details_by_dicta: str
    outcome_by_dicta: str
    model_prediction: dict[str, Any]
    judge_prediction: dict[str, Any]
    prediction_comparison: str
    judge_status: str
    ground_truth_he: str
    reason_for_failure: str
    quality_status: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TopicSubjectV3EventResult:
    event_id: str
    event_index: int
    context: TopicSubjectV3EventContext
    event_payload: dict[str, Any]
    normalized_event: dict[str, Any]
    extraction_payload: dict[str, Any]
    judge_payload: dict[str, Any]
    validation_status: str
    failure_reasons: list[str]
    row_quality: TopicSubjectV3RowQualityData


@dataclass(slots=True)
class TopicSubjectV3ResearchResult:
    run_id: int | None
    topic_tree: dict[str, Any]
    artifacts: list[TopicDecisionArtifact]
    events: list[TopicSubjectV3EventResult]
    row_quality_rows: list[TopicSubjectV3RowQualityData]
    elapsed_seconds: float
    output_paths: dict[str, str] = field(default_factory=dict)

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def candidate_subject_count(self) -> int:
        return sum(1 for item in self.events if item.validation_status != "failed" and topic_subject_v3_is_primary_event(item))

    @property
    def candidate_decision_count(self) -> int:
        return sum(1 for item in self.events if bool(item.event_payload.get("outcome_is_decision")) and item.validation_status != "failed" and topic_subject_v3_is_primary_event(item))

    @property
    def failed_count(self) -> int:
        return sum(1 for row in self.row_quality_rows if row.quality_status in {"model_error", "failed"})


@dataclass(slots=True)
class TopicSubjectResearchResult:
    run_id: int | None
    topic_tree: dict[str, Any]
    artifacts: list[TopicDecisionArtifact]
    extracted_subjects: list[ExtractedTopicSubject]
    quality_rows: list[TopicSubjectQualityRow]
    elapsed_seconds: float
    output_paths: dict[str, str] = field(default_factory=dict)
    event_blocks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def candidate_subject_count(self) -> int:
        return sum(1 for item in self.extracted_subjects if is_countable_subject_event(item))

    @property
    def candidate_decision_count(self) -> int:
        return sum(1 for item in self.extracted_subjects if is_countable_subject_event(item) and bool(item.subject_payload.get("is_decision")))

    @property
    def failed_count(self) -> int:
        return sum(1 for row in self.quality_rows if row.status in {"failed", "model_error", "partial", "no_subject"})


HEBREW_JOIN_STOPWORDS = {
    "על",
    "של",
    "את",
    "עם",
    "או",
    "כל",
    "לא",
    "זה",
    "זו",
    "כך",
    "יש",
    "ראש",
    "סדר",
    "חבר",
    "חברי",
    "חברת",
    "מר",
    "דר",
    "יום",
    "בית",
    "בני",
    "בין",
    "מול",
    "תוך",
    "לפי",
    "שנת",
    "אנו",
    "אני",
}

HEBREW_OCR_PREFIXES = {"ב", "כ", "ל", "מ", "ש", "ה", "ו"}
HEBREW_OCR_LEFT_FRAGMENTS = {"המ", "טי", "אי", "פא", "הסט", "תקצי", "מצ"}
HEBREW_OCR_SUFFIXES = {"ות", "ים", "ית", "יו", "יה", "יהן", "תם", "תי", "נו", "ם", "ן"}


def corrected_hebrew_text(value: Any) -> str:
    """Repair common OCR spacing artifacts without changing audit evidence text."""
    text = compact_text(str(value or "").replace("\u200f", " ").replace("\u200e", " "))
    if not text:
        return ""
    text = re.sub(r"([א-ת])\s*\.\s*([א-ת]{1,3})(?=\b)", r"\1\2", text)
    text = re.sub(r"\s+([,;:!?])", r"\1", text)
    text = re.sub(r"([,(])\s+", r"\1", text)

    def join_three(match: re.Match[str]) -> str:
        left, middle, right = match.group(1), match.group(2), match.group(3)
        if match.start(1) > 0 and match.string[match.start(1) - 1] in {'"', "'", "׳", "״"}:
            return match.group(0)
        if left in HEBREW_JOIN_STOPWORDS:
            return match.group(0)
        if not (left in HEBREW_OCR_LEFT_FRAGMENTS or len(left) <= 2):
            return match.group(0)
        return f"{left}{middle}{right}"

    def join_two(match: re.Match[str]) -> str:
        left, right = match.group(1), match.group(2)
        if match.start(1) > 0 and match.string[match.start(1) - 1] in {'"', "'", "׳", "״"}:
            return match.group(0)
        if left in HEBREW_JOIN_STOPWORDS or right in HEBREW_JOIN_STOPWORDS:
            return match.group(0)
        if left in HEBREW_OCR_PREFIXES and len(right) >= 3:
            return f"{left}{right}"
        if left in HEBREW_OCR_LEFT_FRAGMENTS:
            return f"{left}{right}"
        if right in HEBREW_OCR_SUFFIXES and len(left) >= 3:
            return f"{left}{right}"
        return match.group(0)

    for _ in range(3):
        previous = text
        text = re.sub(r"\b([א-ת]{1,3})\s+([א-ת])\s+([א-ת]{2,})\b", join_three, text)
        text = re.sub(r"\b([א-ת]{1,3})\s+([א-ת]{3,})\b", join_two, text)
        text = compact_text(text)
        if text == previous:
            break
    return text


def corrected_text_for_report(*, artifact: TopicDecisionArtifact, model_payload: dict[str, Any]) -> str:
    return corrected_hebrew_text(artifact.real_text)


def correction_looks_safe(*, raw_text: str, corrected_text: str) -> bool:
    if not corrected_text:
        return False
    raw_norm = normalize_for_search(raw_text)
    corrected_norm = normalize_for_search(corrected_text)
    if not corrected_norm:
        return False
    if len(corrected_norm) < max(12, len(raw_norm) // 4):
        return False
    if len(corrected_norm) > max(200, len(raw_norm) * 2):
        return False
    return shared_context_token_count(raw_norm, corrected_norm) >= 2 or len(raw_norm.split()) <= 5


class TopicSubjectClient(Protocol):
    def extract(self, *, artifact: TopicDecisionArtifact, config: TopicSubjectResearchConfig) -> dict[str, Any]:
        raise NotImplementedError


class TopicSubjectV3Client(Protocol):
    def normalize_event(self, *, context: TopicSubjectV3EventContext, config: TopicSubjectResearchConfig) -> dict[str, Any]:
        raise NotImplementedError

    def extract_event(self, *, context: TopicSubjectV3EventContext, normalized_event: dict[str, Any], config: TopicSubjectResearchConfig) -> dict[str, Any]:
        raise NotImplementedError

    def judge_event(
        self,
        *,
        context: TopicSubjectV3EventContext,
        normalized_event: dict[str, Any],
        extraction_payload: dict[str, Any],
        config: TopicSubjectResearchConfig,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def repair_event_quotes(
        self,
        *,
        context: TopicSubjectV3EventContext,
        normalized_event: dict[str, Any],
        extraction_payload: dict[str, Any],
        quote_failures: list[str],
        config: TopicSubjectResearchConfig,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def assess_event_evidence(
        self,
        *,
        context: TopicSubjectV3EventContext,
        normalized_event: dict[str, Any],
        event_payload: dict[str, Any],
        config: TopicSubjectResearchConfig,
    ) -> dict[str, Any]:
        raise NotImplementedError


class OllamaTopicSubjectClient:
    def extract(self, *, artifact: TopicDecisionArtifact, config: TopicSubjectResearchConfig) -> dict[str, Any]:
        stage = "topic_subject_legacy_extraction"
        model_name = topic_subject_model_for_stage(stage=stage, config=config)
        think = topic_subject_thinking_enabled_for_model(model_name)
        request_payload = topic_subject_prompt_payload(artifact=artifact, max_text_chars=config.max_text_chars)
        body = {
            "model": model_name,
            "stream": False,
            "think": think,
            "format": "json",
            "messages": [
                {
                    "role": "system",
                    "content": topic_subject_system_prompt("You are a Hebrew municipal subject-tree extraction judge. Return strict JSON only.", think=think),
                },
                {"role": "user", "content": json.dumps(request_payload, ensure_ascii=False)},
            ],
            "options": {"temperature": 0.0, "num_predict": 4096},
            "keep_alive": "30m",
        }
        raw_payload, error = self._post_chat_with_timeout_retry(body=body, config=config)
        if error is not None:
            return error

        content = str(((raw_payload.get("message") or {}).get("content")) or "")
        parsed = parse_json_object(content)
        if not isinstance(parsed, dict):
            repaired = self._repair_json(raw_content=content, config=config)
            if isinstance(repaired, dict):
                repaired["raw_payload"] = raw_payload
                repaired["json_repair_applied"] = True
                repaired["stage"] = stage
                repaired["stage_model_name"] = model_name
                repaired["stage_think"] = think
                return repaired
            return {"error_code": "MODEL_INVALID_JSON", "error_text": content[:500], "raw_payload": raw_payload}
        parsed["raw_payload"] = raw_payload
        parsed["stage"] = stage
        parsed["stage_model_name"] = model_name
        parsed["stage_think"] = think
        return parsed

    def _repair_json(self, *, raw_content: str, config: TopicSubjectResearchConfig) -> dict[str, Any] | None:
        if not raw_content.strip():
            return None
        stage = "topic_subject_json_repair"
        model_name = topic_subject_model_for_stage(stage=stage, config=config)
        think = topic_subject_thinking_enabled_for_model(model_name)
        body = {
            "model": model_name,
            "stream": False,
            "think": think,
            "format": "json",
            "messages": [
                {
                    "role": "system",
                    "content": topic_subject_system_prompt("Return valid JSON only. Do not add facts. Do not use markdown.", think=think),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "repair_invalid_json_without_changing_meaning",
                            "instructions": [
                                "Convert the supplied text into valid JSON matching this shape: {corrected_text_he,subjects:[{action_root_label_he,action_child_label_he,subject_matter_he,action_details_he,action_type_evidence_he,action_summary_he,what_text_is_about_he,is_decision,decision,confidence,rationale_he}],overall_summary_he,rationale_he}.",
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
        raw_payload, error = self._post_chat_with_timeout_retry(body=body, config=config)
        if error is not None:
            return None
        content = str(((raw_payload.get("message") or {}).get("content")) or "")
        repaired = parse_json_object(content)
        if isinstance(repaired, dict):
            repaired["stage_model_name"] = model_name
            repaired["stage_think"] = think
        return repaired

    def _post_chat_with_timeout_retry(self, *, body: dict[str, Any], config: TopicSubjectResearchConfig) -> tuple[dict[str, Any], dict[str, Any] | None]:
        attempts = 2
        for attempt_index in range(attempts):
            try:
                timeout = httpx.Timeout(config.timeout_seconds, connect=10.0, read=config.timeout_seconds, write=30.0, pool=10.0)
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(f"{config.ollama_base_url.rstrip('/')}/api/chat", json=body)
                    response.raise_for_status()
                    return response.json(), None
            except httpx.TimeoutException as exc:
                if attempt_index + 1 < attempts:
                    continue
                return {}, {
                    "error_code": "MODEL_REQUEST_TIMEOUT",
                    "error_text": f"{exc.__class__.__name__}:{exc}",
                    "retry_attempts": attempts,
                }
            except Exception as exc:  # noqa: BLE001
                return {}, {"error_code": "MODEL_REQUEST_FAILED", "error_text": f"{exc.__class__.__name__}:{exc}", "retry_attempts": attempt_index + 1}
        return {}, {"error_code": "MODEL_REQUEST_FAILED", "error_text": "unknown_model_request_error", "retry_attempts": attempts}


class OllamaTopicSubjectV3Client(OllamaTopicSubjectClient):
    def normalize_event(self, *, context: TopicSubjectV3EventContext, config: TopicSubjectResearchConfig) -> dict[str, Any]:
        return self._call_stage_json(
            stage="topic_subject_v3_contextual_event_normalization",
            request_payload=topic_subject_v3_normalization_payload(context=context, max_text_chars=config.max_text_chars),
            system_prompt="You normalize Hebrew municipal protocol context into one target event. Return strict JSON only.",
            config=config,
            num_predict=1600,
        )

    def extract_event(self, *, context: TopicSubjectV3EventContext, normalized_event: dict[str, Any], config: TopicSubjectResearchConfig) -> dict[str, Any]:
        return self._call_stage_json(
            stage="topic_subject_v3_action_subject_extraction",
            request_payload=topic_subject_v3_extraction_payload(context=context, normalized_event=normalized_event, config=config),
            system_prompt="You extract one controlled action and concrete subject matter from a normalized Hebrew municipal event. Return strict JSON only.",
            config=config,
            num_predict=1800,
        )

    def judge_event(
        self,
        *,
        context: TopicSubjectV3EventContext,
        normalized_event: dict[str, Any],
        extraction_payload: dict[str, Any],
        config: TopicSubjectResearchConfig,
    ) -> dict[str, Any]:
        return self._call_stage_json(
            stage="topic_subject_v3_event_judge",
            request_payload=topic_subject_v3_judge_payload(context=context, normalized_event=normalized_event, extraction_payload=extraction_payload),
            system_prompt="You are a strict Hebrew municipal extraction judge. Check only supplied evidence. Return strict JSON only.",
            config=config,
            num_predict=1600,
        )

    def repair_event_quotes(
        self,
        *,
        context: TopicSubjectV3EventContext,
        normalized_event: dict[str, Any],
        extraction_payload: dict[str, Any],
        quote_failures: list[str],
        config: TopicSubjectResearchConfig,
    ) -> dict[str, Any]:
        return self._call_stage_json(
            stage="topic_subject_v3_quote_repair",
            request_payload=topic_subject_v3_quote_repair_payload(
                context=context,
                normalized_event=normalized_event,
                extraction_payload=extraction_payload,
                quote_failures=quote_failures,
            ),
            system_prompt="Repair only exact Hebrew evidence quotes from supplied raw rows. Return strict JSON only.",
            config=config,
            num_predict=900,
        )

    def assess_event_evidence(
        self,
        *,
        context: TopicSubjectV3EventContext,
        normalized_event: dict[str, Any],
        event_payload: dict[str, Any],
        config: TopicSubjectResearchConfig,
    ) -> dict[str, Any]:
        return self._call_stage_json(
            stage="topic_subject_v3_evidence_entailment",
            request_payload=topic_subject_v3_evidence_entailment_payload(
                context=context,
                normalized_event=normalized_event,
                event_payload=event_payload,
            ),
            system_prompt="You judge whether a Hebrew municipal extraction is entailed by supplied source evidence. Return strict JSON only.",
            config=config,
            num_predict=1400,
        )

    def _call_stage_json(
        self,
        *,
        stage: str,
        request_payload: dict[str, Any],
        system_prompt: str,
        config: TopicSubjectResearchConfig,
        num_predict: int,
    ) -> dict[str, Any]:
        model_name = topic_subject_model_for_stage(stage=stage, config=config)
        think = topic_subject_thinking_enabled_for_model(model_name)
        body = {
            "model": model_name,
            "stream": False,
            "think": think,
            "format": "json",
            "messages": [
                {"role": "system", "content": topic_subject_system_prompt(system_prompt, think=think)},
                {"role": "user", "content": json.dumps(request_payload, ensure_ascii=False)},
            ],
            "options": {"temperature": 0.0, "num_predict": num_predict},
            "keep_alive": "30m",
        }
        raw_payload, error = self._post_chat_with_timeout_retry(body=body, config=config)
        if error is not None:
            return {**error, "stage": stage}
        content = str(((raw_payload.get("message") or {}).get("content")) or "")
        parsed = parse_json_object(content)
        if not isinstance(parsed, dict):
            repaired = self._repair_stage_json(raw_content=content, stage=stage, config=config)
            if isinstance(repaired, dict):
                repaired["raw_payload"] = raw_payload
                repaired["json_repair_applied"] = True
                repaired["stage"] = stage
                repaired["stage_model_name"] = model_name
                repaired["stage_think"] = think
                return repaired
            return {"error_code": "MODEL_INVALID_JSON", "error_text": content[:500], "raw_payload": raw_payload, "stage": stage}
        parsed["raw_payload"] = raw_payload
        parsed["stage"] = stage
        parsed["stage_model_name"] = model_name
        parsed["stage_think"] = think
        return parsed

    def _repair_stage_json(self, *, raw_content: str, stage: str, config: TopicSubjectResearchConfig) -> dict[str, Any] | None:
        if not raw_content.strip():
            return None
        repair_stage = "topic_subject_v3_json_repair"
        model_name = topic_subject_model_for_stage(stage=repair_stage, config=config)
        think = topic_subject_thinking_enabled_for_model(model_name)
        body = {
            "model": model_name,
            "stream": False,
            "think": think,
            "format": "json",
            "messages": [
                {"role": "system", "content": topic_subject_system_prompt("Return valid JSON only. Do not add facts. Do not use markdown.", think=think)},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "repair_invalid_topic_subject_v3_json_without_changing_meaning",
                            "stage": stage,
                            "instructions": [
                                "Convert the supplied text into valid JSON.",
                                "Do not add or infer new municipal facts.",
                                "Use null, false, empty strings, or empty arrays when the original response omitted a value.",
                            ],
                            "invalid_json_text": raw_content[:6000],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "options": {"temperature": 0.0, "num_predict": 1600},
            "keep_alive": "30m",
        }
        raw_payload, error = self._post_chat_with_timeout_retry(body=body, config=config)
        if error is not None:
            return None
        content = str(((raw_payload.get("message") or {}).get("content")) or "")
        repaired = parse_json_object(content)
        if isinstance(repaired, dict):
            repaired["stage_model_name"] = model_name
            repaired["stage_think"] = think
        return repaired if isinstance(repaired, dict) else None


class MockTopicSubjectClient:
    """Fast local client for verification; production runs should use OllamaTopicSubjectClient."""

    def extract(self, *, artifact: TopicDecisionArtifact, config: TopicSubjectResearchConfig) -> dict[str, Any]:
        text = corrected_hebrew_text(artifact.real_text)
        text_norm = normalize_for_search(text)
        topic = artifact.topic_label_he
        is_request = text_has_any(text_norm, REQUEST_ACTION_CUES) and not text_has_any(text_norm, DECISION_ACTION_CUES)
        is_decision = text_has_any(text_norm, DECISION_ACTION_CUES) and not is_request
        if is_decision:
            is_approval = text_has_any(text_norm, APPROVAL_VERB_CUES)
            decision_label = "אישור" if is_approval else "פעולה עירונית"
            return {
                "corrected_text_he": text,
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
        if text_has_any(text_norm, AGENDA_PROPOSAL_CUES):
            root = "הצעה לסדר יום"
            child = ""
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
            "corrected_text_he": text,
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


class MockTopicSubjectV3Client:
    """Fast local V3 client for shape verification; production runs should use OllamaTopicSubjectV3Client."""

    def normalize_event(self, *, context: TopicSubjectV3EventContext, config: TopicSubjectResearchConfig) -> dict[str, Any]:
        text = corrected_hebrew_text(context.target_artifact.real_text)
        if not compact_text(text):
            return self._non_event(context=context, role="empty_text", reason="אין טקסט לעיבוד.")
        return {
            "context_id": context.context_id,
            "target_artifact_id": context.target_artifact.artifact_id,
            "is_event": True,
            "target_row_role": "action_anchor",
            "event_status": "unknown",
            "normalized_event_summary_he": text[:220],
            "procedural_carrier_he": None,
            "municipal_action_description_he": text[:180],
            "matter_candidate_he": compact_text(context.target_artifact.topic_label_he) or text[:120],
            "outcome_he": None,
            "supporting_quote_he": text[:180],
            "row_roles": [
                {
                    "artifact_id": artifact.artifact_id,
                    "row_role": "action_anchor" if artifact.artifact_id == context.target_artifact.artifact_id else "dependent_detail",
                    "role_reason_he": "mock",
                }
                for artifact in context.rows
            ],
            "confidence": 0.6,
            "rationale_he": "mock",
        }

    def extract_event(self, *, context: TopicSubjectV3EventContext, normalized_event: dict[str, Any], config: TopicSubjectResearchConfig) -> dict[str, Any]:
        if not bool(normalized_event.get("is_event")):
            return {
                "context_id": context.context_id,
                "target_artifact_id": context.target_artifact.artifact_id,
                "is_event": False,
                "confidence": clamp_float(normalized_event.get("confidence"), default=0.0),
                "rationale_he": "mock_non_event",
            }
        text = corrected_hebrew_text(context.target_artifact.real_text)
        text_norm = normalize_for_search(text)
        if has_response_to_inquiry_shape(text_norm):
            root = "מענה לשאילתה"
            confidence = 0.85
        elif has_inquiry_request_shape(text_norm):
            root = "שאילתה"
            confidence = 0.85
        elif text_has_any(text_norm, APPROVAL_VERB_CUES + STRONG_APPROVAL_ACTION_CUES):
            root = "אישור"
            confidence = 0.82
        elif text_has_any(text_norm, REQUEST_ACTION_CUES):
            root = "בקשה"
            confidence = 0.8
        else:
            root = "אחר"
            confidence = 0.42
        outcome_is_decision = root == "אישור"
        outcome = {
            "outcome_type": "approved" if outcome_is_decision else "none",
            "outcome_label_he": root if outcome_is_decision else None,
            "outcome_summary_he": text[:220] if outcome_is_decision else None,
            "outcome_quote_he": text[:220] if outcome_is_decision else None,
            "confidence": confidence if outcome_is_decision else 0.0,
            "limitations": ["mock"],
        }
        matter = compact_text(normalized_event.get("matter_candidate_he") or normalized_event.get("subject_matter_candidate_he")) or text[:120]
        return {
            "context_id": context.context_id,
            "target_artifact_id": context.target_artifact.artifact_id,
            "is_event": True,
            "event_key_he": f"{root}: {matter}",
            "action_type_he": root,
            "action_subtype_he": "",
            "other_action_type_he": "פעולה לא מסווגת" if root == "אחר" else None,
            "action_type_confidence": confidence,
            "matter_he": matter,
            "action_details_he": text[:300],
            "action_quote_he": text[:180],
            "subject_summary_he": f"{root} בנושא {matter}",
            "what_text_is_about_he": text[:300],
            "outcome_is_decision": outcome_is_decision,
            "outcome": outcome,
            "target_row_role": "action_anchor",
            "row_roles": [
                {
                    "artifact_id": artifact.artifact_id,
                    "row_role": "action_anchor" if artifact.artifact_id == context.target_artifact.artifact_id else "dependent_detail",
                    "event_role": "primary" if artifact.artifact_id == context.target_artifact.artifact_id else "supporting",
                    "reason_he": "mock",
                }
                for artifact in context.rows
            ],
            "confidence": confidence,
            "rationale_he": "mock",
        }

    def judge_event(
        self,
        *,
        context: TopicSubjectV3EventContext,
        normalized_event: dict[str, Any],
        extraction_payload: dict[str, Any],
        config: TopicSubjectResearchConfig,
    ) -> dict[str, Any]:
        status = "accepted" if bool(extraction_payload.get("is_event")) else "non_event"
        judge_prediction = {
            "action_type_he": extraction_payload.get("action_type_he") or extraction_payload.get("action_root_label_he"),
            "action_subtype_he": extraction_payload.get("action_subtype_he") or extraction_payload.get("action_child_label_he"),
            "matter_he": extraction_payload.get("matter_he") or extraction_payload.get("subject_matter_he"),
            "outcome_type": ((extraction_payload.get("outcome") or {}).get("outcome_type") if isinstance(extraction_payload.get("outcome"), dict) else None) or ("decision" if extraction_payload.get("is_decision") else "none"),
            "outcome_label_he": ((extraction_payload.get("outcome") or {}).get("outcome_label_he") if isinstance(extraction_payload.get("outcome"), dict) else None),
            "outcome_quote_he": ((extraction_payload.get("outcome") or {}).get("outcome_quote_he") if isinstance(extraction_payload.get("outcome"), dict) else None),
        }
        return {
            "context_id": context.context_id,
            "target_artifact_id": context.target_artifact.artifact_id,
            "judge_prediction": judge_prediction,
            "prediction_comparison": "same",
            "judge_status": "accepted" if status == "accepted" else "rejected",
            "ground_truth_he": "פלט mock לצורך בדיקת צורת V3.",
            "reason_for_failure": "" if status == "accepted" else "non_event",
            "failure_reasons": [] if status == "accepted" else ["non_event"],
            "row_quality": {
                "row_role": extraction_payload.get("target_row_role") or "unknown",
                "event_role": "primary" if status == "accepted" else "not_part_of_event",
                "quality_status": status,
                "ground_truth_he": "פלט mock לצורך בדיקת צורת V3.",
                "reason_for_failure": "" if status == "accepted" else "non_event",
            },
            "confidence": 0.8,
        }

    def repair_event_quotes(
        self,
        *,
        context: TopicSubjectV3EventContext,
        normalized_event: dict[str, Any],
        extraction_payload: dict[str, Any],
        quote_failures: list[str],
        config: TopicSubjectResearchConfig,
    ) -> dict[str, Any]:
        text = corrected_hebrew_text(context.target_artifact.real_text)
        return {
            "action_quote_he": text[:180] if "missing_action_quote" in quote_failures else extraction_payload.get("action_quote_he") or extraction_payload.get("action_evidence_quote_he"),
            "outcome_quote_he": text[:220] if any("outcome" in reason for reason in quote_failures) else ((extraction_payload.get("outcome") or {}).get("outcome_quote_he") if isinstance(extraction_payload.get("outcome"), dict) else None),
            "repair_notes_he": "mock",
        }

    def assess_event_evidence(
        self,
        *,
        context: TopicSubjectV3EventContext,
        normalized_event: dict[str, Any],
        event_payload: dict[str, Any],
        config: TopicSubjectResearchConfig,
    ) -> dict[str, Any]:
        return {
            "context_id": context.context_id,
            "target_artifact_id": context.target_artifact.artifact_id,
            "entailment_status": "entailed",
            "repair_required": False,
            "repaired_event": None,
            "failure_reasons": [],
            "rationale_he": "mock",
        }

    def _non_event(self, *, context: TopicSubjectV3EventContext, role: str, reason: str) -> dict[str, Any]:
        return {
            "context_id": context.context_id,
            "target_artifact_id": context.target_artifact.artifact_id,
            "is_event": False,
            "target_row_role": role,
            "event_status": "not_event",
            "normalized_event_summary_he": reason,
            "procedural_carrier_he": None,
            "municipal_action_description_he": None,
            "matter_candidate_he": None,
            "outcome_he": None,
            "supporting_quote_he": None,
            "row_roles": [{"artifact_id": context.target_artifact.artifact_id, "row_role": role, "role_reason_he": reason}],
            "confidence": 0.9,
            "rationale_he": "mock_non_event",
        }


def topic_subject_prompt_payload(*, artifact: TopicDecisionArtifact, max_text_chars: int) -> dict[str, Any]:
    corrected_text = corrected_hebrew_text(artifact.real_text)
    return {
        "task": "extract_open_subject_tree_candidates_from_accepted_topic_artifact",
        "requirements": [
            "The topic was already accepted by the topic pipeline, but it can be wrong. Do not modify the persisted topic tree; do mark topic_relevance=topic_suspect when the known topic is not supported by the text.",
            "Use corrected_evidence_text to understand the subject. Use raw_evidence_text only for exact source quotes and audit.",
            "When event_block_rows is present, classify the municipal event block as a whole, but keep subject_type_evidence_he and decision.source_quote_he grounded in the current raw_evidence_text anchor row unless the current row is explicitly a countable agenda title.",
            "Use event_block_rows to decide whether nearby title/detail/vote rows belong to the same event; do not create separate subjects for block detail rows.",
            "Identify the procedural/dialogue action that the speaker/body brings to the municipal listener/body, not an incidental content action mentioned inside the text.",
            "Return exactly one primary action plus its subject matter for the current artifact.",
            "Compatibility note: subject_root_label_he and subject_child_label_he are legacy field names. Fill them with the ACTION root/subtype, not the subject matter.",
            "Create open-ended Hebrew action labels in subject_root_label_he and subject_child_label_he. They are not closed lists, but they must be topic-agnostic reusable action labels.",
            "Never include the known topic label, named people, job titles, dates, places, or concrete objects in action root/child labels.",
            "Action labels must be tied to the municipal dialogue/procedural action: inquiry, response to inquiry, request for approval, proposal to agenda, recommendation, report, directive/referral, approval of a decision/protocol/action, or rejection.",
            "subject_root_label_he can be a specific reusable action label such as שאילתה, מענה לשאילתה, בקשה, הצעה לסדר יום, המלצה, דיווח, הנחיה, אישור החלטה, אישור פרוטוקול, דחייה, or הפניה.",
            "subject_child_label_he is optional. Leave it empty when the root action is already specific and a child would be redundant or awkward Hebrew.",
            "Do not use incidental content-action roots such as תיאום, ביצוע, עדכון, הגדרה, פעולה, תרגיל, רישום, נמנע, or הצבעה. Put those actions in subject_object_he/details unless the text itself formally requests, recommends, reports, directs, approves, or rejects them.",
            "If the text lists applicable conditions, scope, rules, eligibility, background, or context without a municipal action/intent, keep those details in subject_object_he/details or artifact_role; do not create הגדרה/הגדרת הקשר or הגדרה/הגדרת תנאים as subject tree nodes.",
            "Do not use domain nouns such as האצלת סמכויות, הסכמים, התקשרויות, סמכות חתימה, חוזים, or הזמנות as subject_root_label_he; put them in subject_object_he/details.",
            "Do not use רקע, מסמך, מטא-מסמך, תאריך, חתימה, or מספר אסמכתא as subject roots. Those are artifact roles, not subjects.",
            "Put the concrete subject matter such as the deficit, funding source, trip, seminar, person, role, contract, committee, date, or legal basis in subject_object_he and subject_details_he, not in action root/child labels.",
            "Do not copy known_topic into subject_object_he unless the current source text directly supports that as the concrete subject matter. If the source topic is broad but the text is about a narrower matter, use the narrower matter.",
            "If raw_evidence_text says במענה לשאילתה/במענה לשאילתא/להלן התייחסותי, the action is מענה לשאילתה, not שאילתה and not בקשה. The subject_object_he should be the matter being answered.",
            "If raw_evidence_text is an inquiry title, אבקש לדעת, or numbered question rows, the action is שאילתה. The subject_object_he should be the question/matter asked about.",
            "Provide subject_type_evidence_he as a short exact phrase from raw_evidence_text that supports the root/child action type.",
            "Return corrected_text_he as a cleaned Hebrew version of raw_evidence_text with OCR spacing/punctuation fixes, without adding facts.",
            "Use the examples only as guidance. Do not copy an example when a better subject label fits the text.",
            "Prefer reusable labels that can group future artifacts, but do not force a generic label if the text has a concrete subject.",
            "A decision is only one possible subject. Requests, proposals, recommendations, reports/responses, directives/referrals, publication/notification, and approvals can be valid subjects only when they describe the municipal dialogue/procedural action.",
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
            "corrected_text_he": "corrected Hebrew version of raw_evidence_text, preserving meaning and not adding facts",
            "subjects": [
                {
                    "action_root_label_he": "action root label, e.g. שאילתה, מענה לשאילתה, בקשה, אישור החלטה",
                    "action_child_label_he": "optional action subtype label, not the subject matter",
                    "subject_matter_he": "concrete subject matter acted on or answered, not a taxonomy label and not copied from known_topic unless supported",
                    "action_details_he": "specific action/subject details such as person, role, place, date, committee, legal basis",
                    "action_type_evidence_he": "short exact phrase from raw_evidence_text supporting the action root/child",
                        "artifact_role": "substantive_subject|background_context|document_date|reference_number|protocol_header|meeting_header|agenda_marker|attachment_reference|contact_info|signature_footer|salutation|empty_text",
                        "topic_relevance": "topic_bearing|not_topic_bearing|topic_suspect",
                        "action_summary_he": "short Hebrew summary of this action and subject matter",
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
        "corrected_evidence_text": corrected_text[: max(500, int(max_text_chars))],
        "decision_context_text": corrected_hebrew_text(artifact.decision_context_text or artifact.real_text)[: max(500, int(max_text_chars))],
        "event_block_rows": [
            {
                "artifact_id": row.get("artifact_id"),
                "source_ordinal": row.get("source_ordinal"),
                "role": row.get("role"),
                "topic_label_he": row.get("topic_label_he"),
                "local_row_role_hint": row.get("local_row_role_hint"),
                "raw_text": str(row.get("raw_text") or "")[:1000],
                "corrected_text": str(row.get("corrected_text") or "")[:1000],
            }
            for row in (artifact.metadata.get("event_block_rows") or [])
            if isinstance(row, dict)
        ],
        "neighbor_contexts": [
            {
                "relation": context.get("relation"),
                "artifact_id": context.get("artifact_id"),
                "page_span": context.get("page_span"),
                "header_path": context.get("header_path"),
                "raw_text": str(context.get("raw_text") or "")[:1000],
                "corrected_decision_context_text": corrected_hebrew_text(context.get("decision_context_text") or context.get("raw_text") or "")[:1000],
            }
            for context in artifact.neighbor_contexts
        ],
    }


def topic_aware_subject_examples(artifact: TopicDecisionArtifact) -> list[dict[str, Any]]:
    topic = compact_text(artifact.topic_label_he) or "הנושא הידוע"
    text_norm = normalize_for_search(corrected_hebrew_text(artifact.real_text))
    examples = [
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
            "subject_child_label_he": "",
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
            "text_pattern": "שאילתה, אבקש לדעת, או שאלות ממוספרות",
            "subject_root_label_he": "שאילתה",
            "subject_child_label_he": "",
            "subject_object_he": "העניין שהשאילתה שואלת עליו, לפי הטקסט עצמו",
            "subject_details_he": "השאלות או הרקע לשאילתה",
            "subject_type_evidence_he": "שאילתה / אבקש לדעת / מי הגורם",
            "artifact_role": "substantive_subject",
            "topic_relevance": "topic_bearing",
            "is_decision": False,
            "why": "שאילתה היא פעולה עירונית מובחנת; אין לסווג אותה כבקשת מידע כללית.",
        },
        {
            "known_topic": topic,
            "text_pattern": "במענה לשאילתה, במענה לשאילתא, להלן התייחסותי",
            "subject_root_label_he": "מענה לשאילתה",
            "subject_child_label_he": "",
            "subject_object_he": "העניין שעליו ניתנת התשובה, לפי הטקסט והקשר השאילתה",
            "subject_details_he": "עיקרי התשובה והנתונים שנמסרו",
            "subject_type_evidence_he": "במענה לשאילתה / להלן התייחסותי",
            "artifact_role": "substantive_subject",
            "topic_relevance": "topic_bearing",
            "is_decision": False,
            "why": "מענה לשאילתה הוא תגובה/תשובה לשאילתה, לא השאילתה עצמה ולא בקשת מידע.",
        },
        {
            "known_topic": topic,
            "text_pattern": "דיון, חילופי דברים, דיווח או עדכון סטטוס ללא הכרעה",
            "subject_root_label_he": "דיווח",
            "subject_child_label_he": "",
            "subject_object_he": topic,
            "subject_details_he": "העניין שנדון או עודכן ללא תוצאת החלטה",
            "subject_type_evidence_he": "דיווח / סקירה / נמסר לוועדה",
            "artifact_role": "substantive_subject",
            "topic_relevance": "topic_bearing",
            "is_decision": False,
            "why": "דיווח או סקירה יכולים להיות פעולה דיונית גם בלי החלטה פורמלית; מענה לשאילתה הוא action נפרד.",
        },
    ]
    if text_has_any(text_norm, AGENDA_PROPOSAL_CUES):
        examples.insert(
            0,
            {
                "known_topic": topic,
                "text_pattern": "הצעה לסדר יום או הצעה להעלות נושא לדיון",
                "subject_root_label_he": "הצעה לסדר יום",
                "subject_child_label_he": "",
                "subject_object_he": topic,
                "subject_details_he": "הנושא שהוצע להעלות לסדר היום והפעולות המבוקשות לדיון",
                "subject_type_evidence_he": "הצעה לסדר יום",
                "artifact_role": "substantive_subject",
                "topic_relevance": "topic_bearing",
                "is_decision": False,
                "why": "הצעה לסדר יום היא כבר תווית פעולה ספציפית ולכן אין צורך בילד כפול או גנרי.",
            },
        )
    if text_has_any(text_norm, CONDITION_SCOPE_CUES):
        examples.insert(
            0,
            {
                "known_topic": topic,
                "text_pattern": "רשימת תנאים, היקף תחולה, כללים או מסגרת פעולה ללא בקשה וללא אישור",
                "subject_root_label_he": "",
                "subject_child_label_he": "",
                "subject_object_he": "האובייקט שעליו חלים התנאים, למשל חוזים/הזמנות/סמכות חתימה",
                "subject_details_he": "התנאים, המסגרת המשפטית, סוגי ההתקשרויות, סכומים או חריגים שמופיעים בטקסט",
                "subject_type_evidence_he": "מחוזה חתום / מכרז / ועדת רכש / הצעות מחיר / הסכום ללא הגבלה",
                "artifact_role": "background_context",
                "topic_relevance": "topic_bearing",
                "is_decision": False,
                "why": "תנאים או היקף תחולה הם פרטי אובייקט/רקע. אין ליצור מהם שורש הגדרה או נושא עצמאי אם אין פעולה עירונית.",
            },
        )
    return [action_subject_example_v2(example) for example in examples]


def action_subject_example_v2(example: dict[str, Any]) -> dict[str, Any]:
    row = dict(example)
    row.setdefault("action_root_label_he", row.get("subject_root_label_he", ""))
    row.setdefault("action_child_label_he", row.get("subject_child_label_he", ""))
    row.setdefault("subject_matter_he", row.get("subject_object_he", ""))
    row.setdefault("action_details_he", row.get("subject_details_he", ""))
    row.setdefault("action_type_evidence_he", row.get("subject_type_evidence_he", ""))
    return row


def topic_subject_v3_context_row(*, artifact: TopicDecisionArtifact, role: str, max_text_chars: int) -> dict[str, Any]:
    # Source-topic fields are intentionally excluded from V3 extraction prompts.
    return {
        "artifact_id": artifact.artifact_id,
        "source_document_version_id": artifact.source_document_version_id,
        "source_ordinal": artifact.source_ordinal,
        "role": role,
        "source_kind": artifact.source_kind,
        "artifact_kind": artifact.artifact_kind,
        "source_title": artifact.source_title,
        "page_span": {"start": artifact.start_page, "end": artifact.end_page},
        "raw_text": artifact.real_text[:max(500, int(max_text_chars))],
        "corrected_text": corrected_hebrew_text(artifact.real_text)[:max(500, int(max_text_chars))],
    }


def topic_subject_v3_source_context(*, context: TopicSubjectV3EventContext, max_text_chars: int) -> dict[str, Any]:
    return {
        "context_id": context.context_id,
        "target_artifact_id": context.target_artifact.artifact_id,
        "target_row": topic_subject_v3_context_row(artifact=context.target_artifact, role="target", max_text_chars=max_text_chars),
        "nearby_rows": [
            topic_subject_v3_context_row(artifact=artifact, role="nearby", max_text_chars=max_text_chars)
            for artifact in context.rows
            if artifact.artifact_id != context.target_artifact.artifact_id
        ],
        "instructions": [
            "Use the target row as the row being judged.",
            "Use nearby rows only to recover bounded event context and row relationships.",
            "Do not create a separate event from neighbor text when the target row is only a fragment, metadata, vote row, or duplicate reference.",
            "No upstream topic labels are supplied here; infer action and subject matter only from the evidence text.",
        ],
    }


def topic_subject_v3_normalization_payload(*, context: TopicSubjectV3EventContext, max_text_chars: int) -> dict[str, Any]:
    return {
        "task": "topic_subject_v3_contextual_event_normalization",
        "pipeline_version": PROVENANCE_V3,
        "requirements": [
            "Return strict JSON only.",
            "Normalize the target row and nearby context into one candidate municipal event before action extraction.",
            "Do not choose a controlled action ontology label in this stage.",
            "Decide whether the target row itself participates in a municipal event, or is only metadata, duplicate reference, evidence fragment, vote/result metadata, supporting phase, or insufficient context.",
            "When nearby rows describe the same event lifecycle, identify the best action anchor row and classify the other rows as supporting rather than independent events.",
            "Separate agenda/procedure carrier from the concrete matter.",
            "Keep שאילתה and מענה לשאילתה distinct when the evidence supports that distinction.",
            "Use nearby rows only to recover bounded subject words, decision/result context, and row roles.",
            "Do not invent municipality-specific rules, labels, or facts.",
        ],
        "schema": {
            "context_id": "string",
            "target_artifact_id": "string",
            "is_event": "boolean",
            "target_row_role": "action_anchor|event_title|dependent_detail|decision_result|vote_metadata|document_fragment|structural_metadata|duplicate_reference|insufficient_context",
            "event_status": "open_request|discussed|approved|rejected|deferred|removed|referred|reported|not_event|unknown",
            "normalized_event_summary_he": "string|null",
            "procedural_carrier_he": "string|null",
            "municipal_action_description_he": "string|null",
            "matter_candidate_he": "string|null",
            "outcome_he": "approved|rejected|referred|removed|deferred|reported|none|unknown|null",
            "supporting_quote_he": "exact quote from one supplied raw_text row|null",
            "row_roles": [
                {
                    "artifact_id": "string",
                    "row_role": "action_anchor|event_title|dependent_detail|decision_result|vote_metadata|document_fragment|structural_metadata|duplicate_reference|insufficient_context",
                    "role_reason_he": "string",
                }
            ],
            "confidence": 0.0,
            "rationale_he": "string",
        },
        "source_context": topic_subject_v3_source_context(context=context, max_text_chars=max_text_chars),
    }


def topic_subject_v3_extraction_payload(
    *,
    context: TopicSubjectV3EventContext,
    normalized_event: dict[str, Any],
    config: TopicSubjectResearchConfig,
) -> dict[str, Any]:
    return {
        "task": "topic_subject_v3_action_matter_outcome_extraction",
        "pipeline_version": PROVENANCE_V3,
        "requirements": [
            "Return strict JSON only.",
            "Use normalized_event as the primary source and source_context only to verify evidence.",
            "Return one primary event for the target row, or is_event=false when the target row is not part of a standalone action/matter event.",
            "Choose action_type_he only by copying an exact label from allowed_actions.",
            f"If the best controlled action type confidence is below {float(config.action_confidence_threshold):.2f}, set action_type_he to אחר and put the best open label in other_action_type_he.",
            "If no controlled label fits with high confidence, set action_type_he to אחר and explain why.",
            "Always return action_type_confidence as a number. Do not omit it when action_type_he is controlled.",
            "Use action_subtype_he only for a reusable subtype; leave it empty when redundant.",
            "Extract only two semantic layers: action_type and matter_he. Do not create a separate formal/effected object field.",
            "matter_he must be the concrete thing acted on, requested, asked about, or answered, including affected participation/person/object when needed to distinguish events.",
            "For requests and approvals, matter_he should state what is requested or approved, not only the broad domain or agenda carrier.",
            "Do not copy an upstream topic because no upstream topic is supplied.",
            "Keep שאילתה and מענה לשאילתה distinct.",
            "Use outcome to describe result/status separately from action_type. For example action_type=בקשה with outcome_type=none, or action_type=אישור with outcome_type=approved.",
            "Set outcome_is_decision=true only when supplied raw text contains an actual outcome such as approval, rejection, referral, removal, or another binding/procedural decision.",
            "If outcome_is_decision=true, outcome.outcome_quote_he must be an exact short quote from a supplied raw_text row.",
            "action_quote_he should be an exact quote from a supplied raw_text row supporting the chosen action type when available.",
        ],
        "schema": {
            "context_id": "string",
            "target_artifact_id": "string",
            "is_event": "boolean",
            "event_key_he": "short stable event key based on action and matter|null",
            "action_type_he": "one exact label from allowed_actions",
            "action_subtype_he": "string|null",
            "other_action_type_he": "string|null",
            "action_type_confidence": 0.0,
            "matter_he": "string|null",
            "action_details_he": "string|null",
            "action_quote_he": "exact quote from supplied raw_text|null",
            "subject_summary_he": "string|null",
            "what_text_is_about_he": "string|null",
            "outcome_is_decision": "boolean",
            "outcome": {
                "outcome_type": "approved|rejected|referred|removed|deferred|reported|none|unknown",
                "outcome_label_he": "string|null",
                "outcome_summary_he": "string|null",
                "outcome_quote_he": "exact quote from supplied raw_text|null",
                "confidence": 0.0,
                "limitations": ["string"],
            },
            "target_row_role": "action_anchor|event_title|dependent_detail|decision_result|vote_metadata|document_fragment|structural_metadata|duplicate_reference|insufficient_context",
            "row_roles": [
                {
                    "artifact_id": "string",
                    "row_role": "action_anchor|event_title|dependent_detail|decision_result|vote_metadata|document_fragment|structural_metadata|duplicate_reference|insufficient_context",
                    "event_role": "primary|supporting|not_part_of_event|duplicate",
                    "reason_he": "string",
                }
            ],
            "confidence": 0.0,
            "rationale_he": "string",
        },
        "allowed_actions": ACTION_ONTOLOGY_V3,
        "action_confidence_threshold": float(config.action_confidence_threshold),
        "normalized_event": compact_payload_for_prompt(normalized_event),
        "source_context": topic_subject_v3_source_context(context=context, max_text_chars=config.max_text_chars),
    }


def topic_subject_v3_judge_payload(
    *,
    context: TopicSubjectV3EventContext,
    normalized_event: dict[str, Any],
    extraction_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "task": "topic_subject_v3_event_judge",
        "pipeline_version": PROVENANCE_V3,
        "requirements": [
            "Return strict JSON only.",
            "Produce judge_prediction as your independent best extraction as if you were the extraction model.",
            "Then compare judge_prediction to extraction_payload and explain differences.",
            "Judge semantic prediction separately from event identity. If the target row is only a request/background/supporting phase for an accepted nearby decision, mark event_identity_status accordingly.",
            "Accept אחר when the model was not highly confident in a controlled action label; that is a conservative valid outcome, not a failure by itself.",
            "Flag source-quote problems when exact quotes are missing or not copied from supplied raw text.",
            "Use row_quality to explain the target row outcome in plain Hebrew.",
        ],
        "schema": {
            "context_id": "string",
            "target_artifact_id": "string",
            "judge_prediction": {
                "is_event": "boolean",
                "action_type_he": "one exact label from allowed_actions|null",
                "action_subtype_he": "string|null",
                "other_action_type_he": "string|null",
                "matter_he": "string|null",
                "outcome_type": "approved|rejected|referred|removed|deferred|reported|none|unknown|null",
                "outcome_label_he": "string|null",
                "outcome_quote_he": "exact quote from supplied raw_text|null",
                "confidence": 0.0,
                "rationale_he": "string",
            },
            "prediction_comparison": "same|partially_different|different|model_invalid|judge_uncertain",
            "event_identity_status": "new_event|same_as_existing_event|supporting_row_only|duplicate_prediction|unknown",
            "judge_status": "accepted|needs_review|rejected",
            "ground_truth_he": "string",
            "reason_for_failure": "string|null",
            "failure_reasons": ["string"],
            "row_quality": {
                "row_role": "string",
                "event_role": "string",
                "quality_status": "accepted|needs_review|failed|non_event",
                "ground_truth_he": "string",
                "reason_for_failure": "string|null",
            },
            "confidence": 0.0,
        },
        "normalized_event": compact_payload_for_prompt(normalized_event),
        "extraction_payload": compact_payload_for_prompt(extraction_payload),
        "allowed_actions": ACTION_ONTOLOGY_V3,
        "source_context": topic_subject_v3_source_context(context=context, max_text_chars=3500),
    }


def topic_subject_v3_quote_repair_payload(
    *,
    context: TopicSubjectV3EventContext,
    normalized_event: dict[str, Any],
    extraction_payload: dict[str, Any],
    quote_failures: list[str],
) -> dict[str, Any]:
    return {
        "task": "topic_subject_v3_exact_quote_repair",
        "pipeline_version": PROVENANCE_V3,
        "quote_failures": quote_failures,
        "requirements": [
            "Return strict JSON only.",
            "Repair only exact quotes by copying text from supplied raw_text rows.",
            "Do not change action, matter, outcome meaning, or confidence.",
            "Use null when no exact quote supports the field.",
        ],
        "schema": {
            "action_quote_he": "exact quote from supplied raw_text|null",
            "outcome_quote_he": "exact quote from supplied raw_text|null",
            "repair_notes_he": "string|null",
        },
        "normalized_event": compact_payload_for_prompt(normalized_event),
        "extraction_payload": compact_payload_for_prompt(extraction_payload),
        "source_context": topic_subject_v3_source_context(context=context, max_text_chars=3500),
    }


def topic_subject_v3_evidence_entailment_payload(
    *,
    context: TopicSubjectV3EventContext,
    normalized_event: dict[str, Any],
    event_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "task": "topic_subject_v3_evidence_entailment",
        "pipeline_version": PROVENANCE_V3,
        "requirements": [
            "Return strict JSON only.",
            "Judge whether action_type_he, matter_he, and outcome are directly entailed by the supplied raw_text rows and normalized_event.",
            "Treat exact quotes as evidence locations only; a grounded quote does not by itself prove the model's action, matter, or outcome interpretation.",
            "Do not infer an approval, rejection, referral, removal, deferral, or other outcome unless a supplied row directly states that outcome.",
            "When repairing action_type_he, choose only an exact label from allowed_actions; use אחר with other_action_type_he when no controlled label is sufficiently supported.",
            "When any field is over-inferred, set repair_required=true and return repaired_event with the best-supported action, matter, and outcome.",
            "If the supplied evidence supports no standalone municipal action/matter event for the target row, return repaired_event.is_event=false with a target_row_role explaining why.",
            "Do not introduce municipality-specific rules or facts not present in source_context.",
        ],
        "schema": {
            "context_id": "string",
            "target_artifact_id": "string",
            "entailment_status": "entailed|partially_entailed|not_entailed|uncertain",
            "field_assessments": {
                "action_type_he": {
                    "status": "entailed|not_entailed|uncertain",
                    "source_quote_he": "exact quote from supplied raw_text|null",
                    "rationale_he": "string",
                },
                "matter_he": {
                    "status": "entailed|not_entailed|uncertain",
                    "source_quote_he": "exact quote from supplied raw_text|null",
                    "rationale_he": "string",
                },
                "outcome": {
                    "status": "entailed|not_entailed|uncertain|not_applicable",
                    "source_quote_he": "exact quote from supplied raw_text|null",
                    "rationale_he": "string",
                },
            },
            "repair_required": "boolean",
            "repaired_event": "full corrected event payload using the extraction schema|null",
            "failure_reasons": ["string"],
            "rationale_he": "string",
        },
        "allowed_actions": ACTION_ONTOLOGY_V3,
        "normalized_event": compact_payload_for_prompt(normalized_event),
        "event_payload": compact_payload_for_prompt(event_payload),
        "source_context": topic_subject_v3_source_context(context=context, max_text_chars=3500),
    }


def compact_payload_for_prompt(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in {"raw_payload"}}


def run_topic_subject_v3_research(
    session: Session,
    *,
    config: TopicSubjectResearchConfig,
    client: TopicSubjectV3Client | None = None,
) -> TopicSubjectV3ResearchResult:
    started = time.perf_counter()
    client = client or OllamaTopicSubjectV3Client()
    topic_tree = load_topic_tree_for_municipality(session, municipality_slug=config.municipality_slug)
    load_limit = (max(0, int(config.offset)) + int(config.limit)) if config.limit is not None else None
    artifacts = load_accepted_topic_artifacts(session, municipality_slug=config.municipality_slug, limit=load_limit)
    if config.offset:
        artifacts = artifacts[max(0, int(config.offset)) :]

    run_row: TopicSubjectV3Run | None = None
    if config.write:
        run_row = TopicSubjectV3Run(
            municipality_slug=config.municipality_slug,
            model_provider="ollama",
            model_name=config.model_name,
            status="running",
            write_mode=True,
            source_artifact_count=len(artifacts),
            metadata_json=json.dumps(
                {
                    "provenance": PROVENANCE_V3,
                    "offset": config.offset,
                    "limit": config.limit,
                    "action_confidence_threshold": config.action_confidence_threshold,
                    "source_topic_hidden_from_extraction": True,
                    "small_model_name": config.small_model_name,
                    "heavy_model_stages": sorted(TOPIC_SUBJECT_HEAVY_MODEL_STAGES),
                    "small_model_stages": sorted(TOPIC_SUBJECT_SMALL_MODEL_STAGES),
                },
                ensure_ascii=False,
            ),
        )
        session.add(run_row)
        session.flush()

    contexts = build_topic_subject_v3_event_contexts(artifacts=artifacts, max_context_rows=config.max_context_rows)
    raw_events: list[TopicSubjectV3EventResult] = []
    row_quality_rows: list[TopicSubjectV3RowQualityData] = []
    for context in contexts:
        event_result, row_quality = process_topic_subject_v3_context(context=context, client=client, config=config)
        if event_result is not None:
            raw_events.append(event_result)
        row_quality_rows.append(row_quality)

    events, row_quality_rows = consolidate_topic_subject_v3_events(events=raw_events, row_quality_rows=row_quality_rows)

    if run_row is not None:
        persist_topic_subject_v3_result(session=session, run=run_row, events=events, row_quality_rows=row_quality_rows)
        run_row.status = "completed"
        run_row.event_count = len(events)
        run_row.candidate_subject_count = sum(1 for item in events if item.validation_status != "failed" and topic_subject_v3_is_primary_event(item))
        run_row.candidate_decision_count = sum(1 for item in events if item.validation_status != "failed" and bool(item.event_payload.get("outcome_is_decision")) and topic_subject_v3_is_primary_event(item))
        run_row.failed_count = sum(1 for row in row_quality_rows if row.quality_status in {"model_error", "failed"})
        run_row.finished_at = datetime.utcnow()
        session.flush()

    result = TopicSubjectV3ResearchResult(
        run_id=int(run_row.id) if run_row is not None else None,
        topic_tree=topic_tree,
        artifacts=artifacts,
        events=events,
        row_quality_rows=row_quality_rows,
        elapsed_seconds=round(time.perf_counter() - started, 3),
    )
    if config.output_dir is not None:
        result.output_paths.update(write_topic_subject_v3_outputs(output_dir=config.output_dir, result=result))
    return result


def build_topic_subject_v3_event_contexts(*, artifacts: list[TopicDecisionArtifact], max_context_rows: int = 5) -> list[TopicSubjectV3EventContext]:
    contexts: list[TopicSubjectV3EventContext] = []
    artifacts_by_docver: dict[int, list[TopicDecisionArtifact]] = {}
    for artifact in artifacts:
        artifacts_by_docver.setdefault(artifact.source_document_version_id, []).append(artifact)
    width = max(1, int(max_context_rows or 5))
    before_count = min(2, max(0, width - 1))
    after_count = max(0, width - before_count - 1)
    for doc_artifacts in artifacts_by_docver.values():
        doc_artifacts.sort(key=lambda item: (item.source_ordinal, item.artifact_id))
        for index, artifact in enumerate(doc_artifacts):
            start = max(0, index - before_count)
            end = min(len(doc_artifacts), index + after_count + 1)
            rows = doc_artifacts[start:end]
            contexts.append(
                TopicSubjectV3EventContext(
                    context_id=topic_subject_v3_context_id(artifact=artifact, rows=rows),
                    target_artifact=artifact,
                    rows=rows,
                )
            )
    return contexts


def topic_subject_v3_context_id(*, artifact: TopicDecisionArtifact, rows: list[TopicDecisionArtifact]) -> str:
    digest = hashlib.sha1(
        "|".join([str(artifact.source_document_version_id), artifact.artifact_id, *[row.artifact_id for row in rows]]).encode("utf-8")
    ).hexdigest()[:16]
    return f"topic_subject_v3_context_{digest}"


def process_topic_subject_v3_context(
    *,
    context: TopicSubjectV3EventContext,
    client: TopicSubjectV3Client,
    config: TopicSubjectResearchConfig,
) -> tuple[TopicSubjectV3EventResult | None, TopicSubjectV3RowQualityData]:
    structural = topic_subject_v3_structural_non_event_payload(context.target_artifact)
    if structural is not None:
        return None, topic_subject_v3_structural_row_quality(context=context, structural=structural)

    normalized_event = client.normalize_event(context=context, config=config)
    if normalized_event.get("error_code"):
        return None, topic_subject_v3_model_error_row_quality(context=context, stage="normalization", model_payload=normalized_event)

    extraction_payload = client.extract_event(context=context, normalized_event=normalized_event, config=config)
    if extraction_payload.get("error_code"):
        return None, topic_subject_v3_model_error_row_quality(context=context, stage="extraction", model_payload=extraction_payload)

    event_payload = normalize_topic_subject_v3_event_payload(
        payload=extraction_payload,
        context=context,
        action_confidence_threshold=config.action_confidence_threshold,
    )
    quote_failures = topic_subject_v3_quote_failures(context=context, event_payload=event_payload)
    if quote_failures and bool(event_payload.get("is_event")):
        repaired = client.repair_event_quotes(
            context=context,
            normalized_event=normalized_event,
            extraction_payload=event_payload,
            quote_failures=quote_failures,
            config=config,
        )
        if repaired.get("error_code"):
            return None, topic_subject_v3_model_error_row_quality(context=context, stage="quote_repair", model_payload=repaired)
        event_payload = normalize_topic_subject_v3_event_payload(
            payload=merge_topic_subject_v3_quote_repair(event_payload=event_payload, repair_payload=repaired),
            context=context,
            action_confidence_threshold=config.action_confidence_threshold,
        )

    evidence_assessment: dict[str, Any] | None = None
    if bool(event_payload.get("is_event")):
        evidence_assessment = client.assess_event_evidence(
            context=context,
            normalized_event=normalized_event,
            event_payload=event_payload,
            config=config,
        )
        if evidence_assessment.get("error_code"):
            return None, topic_subject_v3_model_error_row_quality(context=context, stage="evidence_entailment", model_payload=evidence_assessment)
        event_payload = normalize_topic_subject_v3_event_payload(
            payload=merge_topic_subject_v3_evidence_assessment(event_payload=event_payload, assessment_payload=evidence_assessment),
            context=context,
            action_confidence_threshold=config.action_confidence_threshold,
        )

    local_failures = validate_topic_subject_v3_event_payload(context=context, event_payload=event_payload)
    if evidence_assessment is not None:
        evidence_metadata = event_payload.get("v3_evidence_entailment") if isinstance(event_payload.get("v3_evidence_entailment"), dict) else {}
        if not bool(evidence_metadata.get("repair_applied")):
            local_failures = unique_strings([*local_failures, *topic_subject_v3_unrepaired_evidence_failures(evidence_assessment)])
    judge_payload = client.judge_event(context=context, normalized_event=normalized_event, extraction_payload=event_payload, config=config)
    if judge_payload.get("error_code"):
        return None, topic_subject_v3_model_error_row_quality(context=context, stage="judge", model_payload=judge_payload)

    failure_reasons = unique_strings([*local_failures, *[str(item) for item in judge_payload.get("failure_reasons") or [] if str(item).strip()]])
    validation_status = topic_subject_v3_validation_status(event_payload=event_payload, judge_payload=judge_payload, failure_reasons=failure_reasons)
    event_id = topic_subject_v3_event_id(context=context, event_payload=event_payload)
    row_quality = topic_subject_v3_row_quality_from_payloads(
        context=context,
        event_id=event_id if bool(event_payload.get("is_event")) else "",
        event_payload=event_payload,
        judge_payload=judge_payload,
        validation_status=validation_status,
        failure_reasons=failure_reasons,
    )
    if not bool(event_payload.get("is_event")):
        return None, row_quality

    return (
        TopicSubjectV3EventResult(
            event_id=event_id,
            event_index=0,
            context=context,
            event_payload=event_payload,
            normalized_event=normalized_event,
            extraction_payload=extraction_payload,
            judge_payload=judge_payload,
            validation_status=validation_status,
            failure_reasons=failure_reasons,
            row_quality=row_quality,
        ),
        row_quality,
    )


def topic_subject_v3_structural_non_event_payload(artifact: TopicDecisionArtifact) -> dict[str, str] | None:
    text = compact_text(artifact.real_text)
    if not text:
        return {"row_role": "empty_text", "reason": "הארטיפקט אינו מכיל טקסט."}
    text_norm = normalize_for_search(text)
    if is_date_only_text(text):
        return {"row_role": "document_date", "reason": "הארטיפקט מכיל תאריך בלבד."}
    if is_reference_only_text(text):
        return {"row_role": "reference_number", "reason": "הארטיפקט מכיל מספר אסמכתא או הנחיית תשובה בלבד."}
    if is_attachment_reference_only_text(text_norm):
        return {"row_role": "attachment_reference", "reason": "הארטיפקט מכיל הפניה לנספח/מצורף בלבד."}
    if is_legal_meeting_basis_fragment(text_norm):
        return {"row_role": "legal_meeting_basis_fragment", "reason": "הארטיפקט מכיל רק מסגרת משפטית/סדר יום של ישיבה ללא פעולה קונקרטית."}
    header_role = protocol_or_meeting_header_role(text_norm)
    if header_role:
        return {"row_role": header_role, "reason": "הארטיפקט הוא כותרת פרוטוקול/ישיבה או רשימת משתתפים."}
    if is_contact_info_text(text_norm):
        return {"row_role": "contact_info", "reason": "הארטיפקט מכיל פרטי קשר בלבד."}
    if is_signature_or_footer_text(text_norm):
        return {"row_role": "signature_footer", "reason": "הארטיפקט הוא חתימה או סיום מסמך."}
    return None


def normalize_topic_subject_v3_event_payload(
    *,
    payload: dict[str, Any],
    context: TopicSubjectV3EventContext,
    action_confidence_threshold: float = V3_ACTION_CONFIDENCE_THRESHOLD,
) -> dict[str, Any]:
    raw = payload.get("event") if isinstance(payload.get("event"), dict) else payload
    raw = dict(raw)
    is_event = parse_bool(raw.get("is_event"))
    raw_action_type = compact_text(raw.get("action_type_he") or raw.get("action_root_label_he"))
    if raw_action_type in {"אישור החלטה", "אישור פרוטוקול"}:
        raw_action_type = "אישור"
    schema_warnings: list[str] = []
    action_confidence_source = raw.get("action_type_confidence")
    if action_confidence_source is None:
        action_confidence_source = raw.get("action_label_confidence")
    if action_confidence_source is None:
        if is_event and raw_action_type in ACTION_ONTOLOGY_LABELS_V3:
            schema_warnings.append("missing_action_type_confidence_used_event_confidence")
        action_confidence_source = raw.get("confidence")
    action_confidence = clamp_float(action_confidence_source, default=0.0)
    other_action = compact_text(raw.get("other_action_type_he") or raw.get("other_action_label_he"))
    action_status = "controlled_high_confidence"
    action_type = raw_action_type
    if not is_event:
        action_type = ""
        action_status = "non_event"
    elif raw_action_type not in ACTION_ONTOLOGY_LABELS_V3:
        action_type = "אחר"
        other_action = other_action or raw_action_type or "פעולה לא מסווגת"
        action_status = "other_uncontrolled_label"
    elif raw_action_type != "אחר" and action_confidence < float(action_confidence_threshold):
        action_type = "אחר"
        other_action = other_action or raw_action_type
        action_status = "other_low_confidence"
    elif raw_action_type == "אחר":
        action_type = "אחר"
        other_action = other_action or "פעולה לא מסווגת"
        action_status = "other_by_dicta"

    action_subtype = compact_text(raw.get("action_subtype_he") or raw.get("action_child_label_he"))
    matter = compact_text(raw.get("matter_he") or raw.get("subject_matter_he"))
    outcome_raw = raw.get("outcome") if isinstance(raw.get("outcome"), dict) else {}
    decision_raw = raw.get("decision") if isinstance(raw.get("decision"), dict) else {}
    outcome_is_decision = parse_bool(raw.get("outcome_is_decision") if raw.get("outcome_is_decision") is not None else raw.get("is_decision")) if is_event else False
    outcome_label = compact_text(outcome_raw.get("outcome_label_he") or decision_raw.get("decision_label_he"))[:180]
    outcome = {
        "outcome_type": compact_text(outcome_raw.get("outcome_type"))[:64] or ("decision" if outcome_is_decision else "none"),
        "outcome_label_he": outcome_label,
        "outcome_label_norm": normalize_for_search(outcome_label)[:500] if outcome_label else None,
        "outcome_summary_he": compact_text(outcome_raw.get("outcome_summary_he") or decision_raw.get("decision_summary_he"))[:700],
        "outcome_quote_he": compact_text(outcome_raw.get("outcome_quote_he") or decision_raw.get("source_quote_he"))[:1000],
        "confidence": clamp_float(outcome_raw.get("confidence") if outcome_raw.get("confidence") is not None else decision_raw.get("confidence"), default=0.0),
        "limitations": [compact_text(item) for item in outcome_raw.get("limitations") or decision_raw.get("limitations") or [] if compact_text(item)],
    }
    if not outcome_is_decision and not any(value for key, value in outcome.items() if key != "limitations"):
        outcome = {}
    normalized = {
        "context_id": compact_text(raw.get("context_id")) or context.context_id,
        "target_artifact_id": compact_text(raw.get("target_artifact_id")) or context.target_artifact.artifact_id,
        "is_event": is_event,
        "event_key_he": compact_text(raw.get("event_key_he"))[:220],
        "action_type_he": action_type,
        "action_type_norm": normalize_for_search(action_type)[:500] if action_type else "",
        "action_subtype_he": action_subtype,
        "action_subtype_norm": normalize_for_search(action_subtype)[:500] if action_subtype else "",
        "other_action_type_he": other_action,
        "other_action_type_norm": normalize_for_search(other_action)[:500] if other_action else "",
        "action_type_confidence": action_confidence,
        "action_type_status": action_status,
        "matter_he": matter[:500],
        "matter_norm": normalize_for_search(matter)[:500] if matter else "",
        "action_details_he": compact_text(raw.get("action_details_he"))[:1000],
        "action_quote_he": compact_text(raw.get("action_quote_he") or raw.get("action_evidence_quote_he"))[:700],
        "subject_summary_he": compact_text(raw.get("subject_summary_he"))[:700],
        "what_text_is_about_he": compact_text(raw.get("what_text_is_about_he"))[:700],
        "outcome_is_decision": outcome_is_decision,
        "outcome": outcome if outcome_is_decision or outcome else None,
        "target_row_role": compact_text(raw.get("target_row_role"))[:64] or "unknown",
        "row_roles": raw.get("row_roles") if isinstance(raw.get("row_roles"), list) else [],
        "confidence": clamp_float(raw.get("confidence"), default=0.0),
        "rationale_he": compact_text(raw.get("rationale_he"))[:1000],
        "schema_warnings": schema_warnings,
        "event_identity_status": compact_text(raw.get("event_identity_status")) or "unknown",
        "v3_quote_repair": raw.get("v3_quote_repair") if isinstance(raw.get("v3_quote_repair"), dict) else None,
        "v3_outcome_quote_repair": raw.get("v3_outcome_quote_repair") if isinstance(raw.get("v3_outcome_quote_repair"), dict) else None,
        "v3_evidence_entailment": raw.get("v3_evidence_entailment") if isinstance(raw.get("v3_evidence_entailment"), dict) else None,
        "raw_model_payload": compact_payload_for_prompt(payload),
    }
    return repair_topic_subject_v3_request_outcome_payload(context=context, event_payload=normalized)


def repair_topic_subject_v3_request_outcome_payload(*, context: TopicSubjectV3EventContext, event_payload: dict[str, Any]) -> dict[str, Any]:
    if not bool(event_payload.get("is_event")) or not bool(event_payload.get("outcome_is_decision")):
        return event_payload
    outcome = event_payload.get("outcome") if isinstance(event_payload.get("outcome"), dict) else {}
    quote = compact_text(outcome.get("outcome_quote_he"))
    classification = topic_subject_v3_outcome_quote_classification(quote)
    repaired = dict(event_payload)
    metadata = dict(repaired.get("v3_outcome_quote_repair") or {})
    metadata["outcome_quote_classification"] = classification
    if classification == "request_for_outcome" and topic_subject_v3_target_text_request_like_without_decision(context.target_artifact.real_text):
        repaired_outcome = dict(outcome)
        repaired_outcome["outcome_type"] = "none"
        repaired_outcome["outcome_label_he"] = ""
        repaired_outcome["outcome_label_norm"] = ""
        repaired_outcome["outcome_summary_he"] = ""
        repaired_outcome["outcome_quote_he"] = ""
        repaired_outcome["request_quote_he"] = quote
        repaired["outcome_is_decision"] = False
        repaired["outcome"] = repaired_outcome
        metadata["repair_reason"] = "outcome_quote_is_request_for_approval_not_actual_outcome"
        if compact_text(repaired.get("action_type_he")) == "אישור":
            repaired["action_type_he"] = "בקשה"
            repaired["action_type_norm"] = normalize_for_search("בקשה")[:500]
            repaired["action_type_status"] = "repaired_request_phase_from_approval_quote"
    repaired["v3_outcome_quote_repair"] = metadata
    return repaired


def topic_subject_v3_outcome_quote_classification(quote: str) -> str:
    quote_norm = normalize_for_search(quote)
    if not quote_norm:
        return "unknown"
    if text_has_any(quote_norm, APPROVAL_DECISION_QUOTE_CUES + AGENDA_REMOVAL_CUES + COMMITTEE_REFERRAL_CUES):
        return "actual_outcome"
    if text_has_any(quote_norm, REQUEST_ACTION_CUES):
        return "request_for_outcome"
    if text_has_any(quote_norm, DECISION_ACTION_CUES + APPROVAL_VERB_CUES):
        return "actual_outcome"
    return "unknown"


def topic_subject_v3_quote_failures(*, context: TopicSubjectV3EventContext, event_payload: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    action_quote = compact_text(event_payload.get("action_quote_he"))
    if action_quote and not topic_subject_v3_quote_supported(context=context, quote=action_quote):
        failures.append("action_quote_not_grounded")
    if bool(event_payload.get("outcome_is_decision")):
        outcome = event_payload.get("outcome") if isinstance(event_payload.get("outcome"), dict) else {}
        source_quote = compact_text(outcome.get("outcome_quote_he"))
        if not source_quote:
            failures.append("missing_outcome_quote")
        elif not topic_subject_v3_quote_supported(context=context, quote=source_quote):
            failures.append("outcome_quote_not_grounded")
    return failures


def topic_subject_v3_quote_supported(*, context: TopicSubjectV3EventContext, quote: str) -> bool:
    return any(quote_supported_by_text(quote=quote, text=artifact.real_text) for artifact in context.rows)


def merge_topic_subject_v3_quote_repair(*, event_payload: dict[str, Any], repair_payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(event_payload)
    action_quote = compact_text(repair_payload.get("action_quote_he") or repair_payload.get("action_evidence_quote_he"))
    if action_quote:
        merged["action_quote_he"] = action_quote
    outcome_quote = compact_text(repair_payload.get("outcome_quote_he") or repair_payload.get("decision_source_quote_he"))
    if outcome_quote:
        outcome = dict(merged.get("outcome") or {})
        outcome["outcome_quote_he"] = outcome_quote
        merged["outcome"] = outcome
    metadata = dict(merged.get("v3_quote_repair") or {})
    metadata.update({key: value for key, value in repair_payload.items() if key != "raw_payload"})
    merged["v3_quote_repair"] = metadata
    return merged


def merge_topic_subject_v3_evidence_assessment(*, event_payload: dict[str, Any], assessment_payload: dict[str, Any]) -> dict[str, Any]:
    repaired_event = assessment_payload.get("repaired_event") if isinstance(assessment_payload.get("repaired_event"), dict) else None
    repair_applied = parse_bool(assessment_payload.get("repair_required")) and repaired_event is not None
    merged = dict(event_payload)
    if repair_applied:
        merged.update(repaired_event)
    metadata = dict(merged.get("v3_evidence_entailment") or {})
    metadata.update({key: value for key, value in assessment_payload.items() if key not in {"raw_payload", "repaired_event"}})
    metadata["repair_applied"] = repair_applied
    if repaired_event is not None:
        metadata["repaired_event"] = compact_payload_for_prompt(repaired_event)
    merged["v3_evidence_entailment"] = metadata
    return merged


def topic_subject_v3_unrepaired_evidence_failures(assessment_payload: dict[str, Any]) -> list[str]:
    status = compact_text(assessment_payload.get("entailment_status"))
    repair_required = parse_bool(assessment_payload.get("repair_required"))
    repaired_event = assessment_payload.get("repaired_event") if isinstance(assessment_payload.get("repaired_event"), dict) else None
    if repair_required and repaired_event is None:
        explicit = [compact_text(item) for item in assessment_payload.get("failure_reasons") or [] if compact_text(item)]
        return unique_strings(explicit or ["evidence_entailment_repair_missing"])
    if status in {"not_entailed", "uncertain"}:
        explicit = [compact_text(item) for item in assessment_payload.get("failure_reasons") or [] if compact_text(item)]
        return unique_strings(explicit or [f"evidence_entailment_{status}"])
    return []


def validate_topic_subject_v3_event_payload(*, context: TopicSubjectV3EventContext, event_payload: dict[str, Any]) -> list[str]:
    if not bool(event_payload.get("is_event")):
        return []
    failures: list[str] = []
    if not compact_text(event_payload.get("action_type_he")):
        failures.append("missing_action_type")
    if not compact_text(event_payload.get("matter_he")):
        failures.append("missing_matter")
    failures.extend(topic_subject_v3_quote_failures(context=context, event_payload=event_payload))
    return unique_strings(failures)


def topic_subject_v3_validation_status(*, event_payload: dict[str, Any], judge_payload: dict[str, Any], failure_reasons: list[str]) -> str:
    if not bool(event_payload.get("is_event")):
        return "non_event"
    judge_status = compact_text(judge_payload.get("judge_status"))
    if failure_reasons:
        return "failed"
    if judge_status == "rejected":
        return "failed"
    if judge_status == "needs_review":
        return "needs_review"
    return "accepted"


def topic_subject_v3_event_group_key(*, context: TopicSubjectV3EventContext, event_payload: dict[str, Any]) -> str:
    explicit = compact_text(event_payload.get("event_key_he"))
    if explicit:
        return normalize_for_search(explicit)
    parts = [
        event_payload.get("action_type_he"),
        event_payload.get("other_action_type_he"),
        event_payload.get("action_subtype_he"),
        event_payload.get("matter_he"),
        event_payload.get("subject_summary_he"),
    ]
    key = normalize_for_search(" | ".join(compact_text(part) for part in parts if compact_text(part)))
    return key or normalize_for_search(context.target_artifact.artifact_id)


def topic_subject_v3_event_id(*, context: TopicSubjectV3EventContext, event_payload: dict[str, Any]) -> str:
    group_key = topic_subject_v3_event_group_key(context=context, event_payload=event_payload)
    digest = hashlib.sha1(f"{context.target_artifact.source_document_version_id}|{group_key}".encode("utf-8")).hexdigest()[:16]
    return f"topic_subject_v3_event_{digest}"


def topic_subject_v3_row_quality_from_payloads(
    *,
    context: TopicSubjectV3EventContext,
    event_id: str,
    event_payload: dict[str, Any],
    judge_payload: dict[str, Any],
    validation_status: str,
    failure_reasons: list[str],
) -> TopicSubjectV3RowQualityData:
    target = context.target_artifact
    row_quality = judge_payload.get("row_quality") if isinstance(judge_payload.get("row_quality"), dict) else {}
    outcome_summary = topic_subject_v3_outcome_summary(event_payload)
    is_event = bool(event_payload.get("is_event"))
    quality_status = compact_text(row_quality.get("quality_status")) or validation_status
    if not is_event:
        quality_status = "non_event"
    elif validation_status == "failed":
        quality_status = "failed"
    elif validation_status == "needs_review":
        quality_status = "needs_review"
    judge_status = compact_text(judge_payload.get("judge_status")) or validation_status
    if not is_event and judge_status == "accepted":
        judge_status = "non_event"
    ground_truth = compact_text(row_quality.get("ground_truth_he") or judge_payload.get("ground_truth_he"))
    if not ground_truth and not is_event:
        ground_truth = "הטקסט נשפט כקטע שאינו אירוע פעולה/נושא עצמאי."
    reason_items = [*(failure_reasons or []), compact_text(row_quality.get("reason_for_failure") or judge_payload.get("reason_for_failure"))]
    if is_event and compact_text(event_payload.get("action_type_he")) == "אישור" and not bool(event_payload.get("outcome_is_decision")):
        reason_items.append("approval_action_without_decision_outcome")
    return TopicSubjectV3RowQualityData(
        artifact=target,
        event_id=event_id,
        row_role=compact_text(row_quality.get("row_role")) or compact_text(event_payload.get("target_row_role")) or "unknown",
        event_role=compact_text(row_quality.get("event_role")) or ("primary" if is_event else "not_part_of_event"),
        topic_relevance="not_judged_source_topic_hidden_from_extraction",
        action_type_by_dicta=compact_text(event_payload.get("action_type_he")),
        action_subtype_by_dicta=compact_text(event_payload.get("action_subtype_he")),
        other_action_type_by_dicta=compact_text(event_payload.get("other_action_type_he")),
        matter_by_dicta=compact_text(event_payload.get("matter_he")),
        action_details_by_dicta=compact_text(event_payload.get("action_details_he")),
        outcome_by_dicta=outcome_summary,
        model_prediction=event_payload,
        judge_prediction=topic_subject_v3_judge_prediction(judge_payload),
        prediction_comparison=compact_text(judge_payload.get("prediction_comparison")) or "unknown",
        judge_status=judge_status,
        ground_truth_he=ground_truth,
        reason_for_failure="; ".join(unique_strings(reason_items)),
        quality_status=quality_status,
        metadata={
            "context_id": context.context_id,
            "context_artifact_ids": [artifact.artifact_id for artifact in context.rows],
            "source_topic_hidden_from_extraction": True,
            "source_topic_label_he": target.topic_label_he,
            "event_identity_status": compact_text(judge_payload.get("event_identity_status")) or compact_text(event_payload.get("event_identity_status")) or "unknown",
            "schema_warnings": [str(item) for item in event_payload.get("schema_warnings") or [] if str(item).strip()],
            "judge_payload": compact_payload_for_prompt(judge_payload),
        },
    )


def topic_subject_v3_outcome_summary(event_payload: dict[str, Any]) -> str:
    if not bool(event_payload.get("outcome_is_decision")):
        return ""
    outcome = event_payload.get("outcome") if isinstance(event_payload.get("outcome"), dict) else {}
    return " | ".join(
        unique_strings(
            [
                compact_text(outcome.get("outcome_label_he")),
                compact_text(outcome.get("outcome_summary_he")),
                compact_text(outcome.get("outcome_quote_he")),
            ]
        )
    )


def topic_subject_v3_judge_prediction(judge_payload: dict[str, Any]) -> dict[str, Any]:
    prediction = judge_payload.get("judge_prediction") if isinstance(judge_payload.get("judge_prediction"), dict) else {}
    if not prediction:
        return {}
    return {
        "is_event": parse_bool(prediction.get("is_event")),
        "action_type_he": compact_text(prediction.get("action_type_he") or prediction.get("action_root_label_he")),
        "action_subtype_he": compact_text(prediction.get("action_subtype_he") or prediction.get("action_child_label_he")),
        "other_action_type_he": compact_text(prediction.get("other_action_type_he") or prediction.get("other_action_label_he")),
        "matter_he": compact_text(prediction.get("matter_he") or prediction.get("subject_matter_he")),
        "outcome_type": compact_text(prediction.get("outcome_type")),
        "outcome_label_he": compact_text(prediction.get("outcome_label_he") or prediction.get("decision_label_he")),
        "outcome_quote_he": compact_text(prediction.get("outcome_quote_he") or prediction.get("source_quote_he")),
        "confidence": clamp_float(prediction.get("confidence"), default=0.0),
        "rationale_he": compact_text(prediction.get("rationale_he"))[:1000],
    }


def topic_subject_v3_structural_row_quality(*, context: TopicSubjectV3EventContext, structural: dict[str, str]) -> TopicSubjectV3RowQualityData:
    artifact = context.target_artifact
    reason = compact_text(structural.get("reason"))
    row_role = compact_text(structural.get("row_role")) or "structural_metadata"
    return TopicSubjectV3RowQualityData(
        artifact=artifact,
        event_id="",
        row_role=row_role,
        event_role="not_part_of_event",
        topic_relevance="not_judged_source_topic_hidden_from_extraction",
        action_type_by_dicta="",
        action_subtype_by_dicta="",
        other_action_type_by_dicta="",
        matter_by_dicta="",
        action_details_by_dicta="",
        outcome_by_dicta="",
        model_prediction={"is_event": False, "row_role": row_role, "reason": reason, "structural_guard": True},
        judge_prediction={},
        prediction_comparison="not_applicable",
        judge_status="structural_non_event",
        ground_truth_he=reason,
        reason_for_failure="",
        quality_status="non_event",
        metadata={
            "context_id": context.context_id,
            "source_topic_hidden_from_extraction": True,
            "source_topic_label_he": artifact.topic_label_he,
        },
    )


def topic_subject_v3_model_error_row_quality(*, context: TopicSubjectV3EventContext, stage: str, model_payload: dict[str, Any]) -> TopicSubjectV3RowQualityData:
    artifact = context.target_artifact
    reason = str(model_payload.get("error_text") or model_payload.get("error_code") or "model_error")
    return TopicSubjectV3RowQualityData(
        artifact=artifact,
        event_id="",
        row_role="model_error",
        event_role="unknown",
        topic_relevance="unknown",
        action_type_by_dicta="",
        action_subtype_by_dicta="",
        other_action_type_by_dicta="",
        matter_by_dicta="",
        action_details_by_dicta="",
        outcome_by_dicta="",
        model_prediction=compact_payload_for_prompt(model_payload),
        judge_prediction={},
        prediction_comparison="not_available",
        judge_status="model_error",
        ground_truth_he="לא ניתן לשפוט כי קריאת Dicta נכשלה.",
        reason_for_failure=reason,
        quality_status="model_error",
        metadata={
            "context_id": context.context_id,
            "stage": stage,
            "source_topic_hidden_from_extraction": True,
            "source_topic_label_he": artifact.topic_label_he,
        },
    )


def consolidate_topic_subject_v3_events(
    *,
    events: list[TopicSubjectV3EventResult],
    row_quality_rows: list[TopicSubjectV3RowQualityData],
) -> tuple[list[TopicSubjectV3EventResult], list[TopicSubjectV3RowQualityData]]:
    grouped: list[list[TopicSubjectV3EventResult]] = []
    for event in sorted(events, key=lambda item: (item.context.target_artifact.source_document_version_id, item.context.target_artifact.source_ordinal, item.event_id)):
        for group in grouped:
            if any(topic_subject_v3_events_represent_same_event(left=member, right=event) for member in group):
                group.append(event)
                break
        else:
            grouped.append([event])

    selected_events: list[TopicSubjectV3EventResult] = []
    event_id_by_old: dict[str, str] = {}
    for group_events in grouped:
        group_events.sort(key=topic_subject_v3_event_rank, reverse=True)
        primary = topic_subject_v3_primary_event_for_group(group_events)
        primary.event_payload = topic_subject_v3_apply_canonical_matter(primary=primary, group_events=group_events)
        event_id = topic_subject_v3_event_id(context=primary.context, event_payload=primary.event_payload)
        primary.event_id = event_id
        primary.event_index = len(selected_events)
        primary.row_quality.event_id = event_id
        primary.row_quality.event_role = "primary"
        if primary.row_quality.quality_status == "accepted":
            primary.row_quality.quality_status = "accepted_primary"
        primary.row_quality.metadata = {
            **primary.row_quality.metadata,
            "event_identity_status": "primary_event",
            "merged_artifact_ids": [item.context.target_artifact.artifact_id for item in group_events],
            "anchor_selection_reason": topic_subject_v3_anchor_selection_reason(primary),
        }
        primary.event_payload = {
            **primary.event_payload,
            "event_identity_status": "primary_event",
            "merged_artifact_ids": [item.context.target_artifact.artifact_id for item in group_events],
            "duplicate_artifact_ids": [item.context.target_artifact.artifact_id for item in group_events if item is not primary and topic_subject_v3_secondary_event_role(primary=primary, secondary=item) == "duplicate_event_prediction"],
            "supporting_phase_artifact_ids": [item.context.target_artifact.artifact_id for item in group_events if item is not primary and topic_subject_v3_secondary_event_role(primary=primary, secondary=item) == "supporting_phase"],
        }
        primary.context.rows = topic_subject_v3_merged_context_rows(group_events)
        primary.event_payload["row_roles"] = topic_subject_v3_merged_row_roles(group_events=group_events, primary=primary)
        selected_events.append(primary)
        for item in group_events:
            event_id_by_old[item.event_id] = event_id
            item.row_quality.event_id = event_id
            if item is not primary:
                role = topic_subject_v3_secondary_event_role(primary=primary, secondary=item)
                identity_status = "supporting_row_only" if role == "supporting_phase" else "same_as_existing_event"
                item.row_quality.event_role = role
                if item.row_quality.quality_status == "accepted" or topic_subject_v3_can_suppress_secondary_failure(item):
                    suppressed_reason = item.row_quality.reason_for_failure
                    item.row_quality.quality_status = "accepted_supporting_phase" if role == "supporting_phase" else "accepted_duplicate"
                    if suppressed_reason:
                        item.row_quality.metadata = {
                            **item.row_quality.metadata,
                            "suppressed_secondary_failure_reason": suppressed_reason,
                        }
                        item.row_quality.reason_for_failure = ""
                item.row_quality.metadata = {
                    **item.row_quality.metadata,
                    "duplicate_of_event_id": event_id,
                    "event_identity_status": identity_status,
                    "event_identity_reason": topic_subject_v3_event_identity_reason(primary=primary, secondary=item),
                }

    for row in row_quality_rows:
        if row.event_id in event_id_by_old:
            row.event_id = event_id_by_old[row.event_id]
    selected_events.sort(key=lambda item: (item.context.target_artifact.source_document_version_id, item.context.target_artifact.source_ordinal, item.event_id))
    for index, event in enumerate(selected_events):
        event.event_index = index
    return selected_events, row_quality_rows


def topic_subject_v3_can_suppress_secondary_failure(event: TopicSubjectV3EventResult) -> bool:
    if event.row_quality.quality_status not in {"failed", "needs_review"}:
        return False
    reasons = set(event.failure_reasons or []) | {reason.strip() for reason in event.row_quality.reason_for_failure.split(";") if reason.strip()}
    return bool(reasons) and reasons <= {"action_quote_not_grounded", "outcome_quote_not_grounded", "missing_outcome_quote"}


def topic_subject_v3_primary_event_for_group(group_events: list[TopicSubjectV3EventResult]) -> TopicSubjectV3EventResult:
    return max(group_events, key=topic_subject_v3_anchor_rank)


def topic_subject_v3_anchor_rank(event: TopicSubjectV3EventResult) -> tuple[float, float, float, float, float]:
    target_text = event.context.target_artifact.real_text
    outcome_quote = topic_subject_v3_decision_quote(event.event_payload)
    action_quote = compact_text(event.event_payload.get("action_quote_he"))
    quote_in_target = 0.0
    if outcome_quote and quote_supported_by_text(quote=outcome_quote, text=target_text):
        quote_in_target += 2.0
    if action_quote and quote_supported_by_text(quote=action_quote, text=target_text):
        quote_in_target += 1.0
    decision_evidence = 1.0 if topic_subject_v3_target_text_has_explicit_decision_evidence(target_text) else 0.0
    request_penalty = -1.0 if topic_subject_v3_target_text_request_like_without_decision(target_text) else 0.0
    confidence = clamp_float(event.event_payload.get("confidence"), default=0.0)
    source_order = -float(event.context.target_artifact.source_ordinal or 0)
    return quote_in_target, decision_evidence, request_penalty, confidence, source_order


def topic_subject_v3_anchor_selection_reason(event: TopicSubjectV3EventResult) -> str:
    target_text = event.context.target_artifact.real_text
    outcome_quote = topic_subject_v3_decision_quote(event.event_payload)
    if outcome_quote and quote_supported_by_text(quote=outcome_quote, text=target_text):
        return "target_row_contains_outcome_quote"
    action_quote = compact_text(event.event_payload.get("action_quote_he"))
    if action_quote and quote_supported_by_text(quote=action_quote, text=target_text):
        return "target_row_contains_action_quote"
    if topic_subject_v3_target_text_has_explicit_decision_evidence(target_text):
        return "target_row_has_explicit_decision_evidence"
    return "best_available_event_rank"


def topic_subject_v3_target_text_has_explicit_decision_evidence(text: str) -> bool:
    text_norm = normalize_for_search(corrected_hebrew_text(text))
    return text_has_any(text_norm, APPROVAL_DECISION_QUOTE_CUES + AGENDA_REMOVAL_CUES + COMMITTEE_REFERRAL_CUES)


def topic_subject_v3_apply_canonical_matter(*, primary: TopicSubjectV3EventResult, group_events: list[TopicSubjectV3EventResult]) -> dict[str, Any]:
    payload = dict(primary.event_payload)
    candidates = [
        {
            "matter_he": compact_text(event.event_payload.get("matter_he")),
            "artifact_id": event.context.target_artifact.artifact_id,
            "score": topic_subject_v3_matter_candidate_score(event),
        }
        for event in group_events
        if compact_text(event.event_payload.get("matter_he"))
    ]
    if not candidates:
        return payload
    best = max(candidates, key=lambda item: (float(item["score"]), len(topic_subject_v3_identity_tokens(str(item["matter_he"])))) )
    matter = compact_text(best.get("matter_he"))
    payload["matter_he"] = matter[:500]
    payload["matter_norm"] = normalize_for_search(matter)[:500] if matter else ""
    payload["canonical_matter_source_artifact_id"] = compact_text(best.get("artifact_id"))
    payload["canonical_matter_candidates"] = candidates
    return payload


def topic_subject_v3_matter_candidate_score(event: TopicSubjectV3EventResult) -> float:
    matter = compact_text(event.event_payload.get("matter_he"))
    score = float(len(topic_subject_v3_identity_tokens(matter))) * 0.1
    if topic_subject_v3_target_text_has_explicit_decision_evidence(event.context.target_artifact.real_text):
        score += 1.0
    if topic_subject_v3_target_row_request_like_without_decision(event):
        score -= 0.7
    if any(token in normalize_for_search(matter) for token in ("השתתפות", "נסיעה", "מינוי", "הסכם", "תבחין", "תקציב")):
        score += 0.25
    return score


def topic_subject_v3_is_primary_event(event: TopicSubjectV3EventResult) -> bool:
    identity_status = compact_text(event.event_payload.get("event_identity_status"))
    row_role = compact_text(event.row_quality.event_role)
    return identity_status in {"", "unknown", "primary_event", "new_event"} and row_role not in {"duplicate_event_prediction", "supporting_phase", "supporting_context", "same_as_existing_event"}


def topic_subject_v3_events_represent_same_event(*, left: TopicSubjectV3EventResult, right: TopicSubjectV3EventResult) -> bool:
    if left.context.target_artifact.source_document_version_id != right.context.target_artifact.source_document_version_id:
        return False
    left_quote = topic_subject_v3_outcome_quote_norm(left.event_payload)
    right_quote = topic_subject_v3_outcome_quote_norm(right.event_payload)
    if left_quote and left_quote == right_quote:
        return True
    if not topic_subject_v3_contexts_overlap(left=left, right=right):
        return False
    if topic_subject_v3_request_phase_matches_decision(left=left, right=right):
        return True
    matter_similarity = topic_subject_v3_text_similarity(
        compact_text(left.event_payload.get("matter_he") or left.event_payload.get("event_key_he")),
        compact_text(right.event_payload.get("matter_he") or right.event_payload.get("event_key_he")),
    )
    if matter_similarity >= 0.55 and topic_subject_v3_actions_compatible(left.event_payload, right.event_payload):
        return True
    if (
        bool(left.event_payload.get("outcome_is_decision"))
        and bool(right.event_payload.get("outcome_is_decision"))
        and (topic_subject_v3_target_row_request_like_without_decision(left) or topic_subject_v3_target_row_request_like_without_decision(right))
        and (topic_subject_v3_has_explicit_decision_evidence(left) or topic_subject_v3_has_explicit_decision_evidence(right))
        and topic_subject_v3_shared_evidence_similarity(left=left, right=right) >= 0.25
    ):
        return True
    if (bool(left.event_payload.get("outcome_is_decision")) or bool(right.event_payload.get("outcome_is_decision"))) and topic_subject_v3_shared_evidence_similarity(left=left, right=right) >= 0.35:
        return True
    return False


def topic_subject_v3_request_phase_matches_decision(*, left: TopicSubjectV3EventResult, right: TopicSubjectV3EventResult) -> bool:
    pairs = ((left, right), (right, left))
    for request_event, decision_event in pairs:
        if not topic_subject_v3_target_row_request_like_without_decision(request_event):
            continue
        if not bool(decision_event.event_payload.get("outcome_is_decision")) or not topic_subject_v3_has_explicit_decision_evidence(decision_event):
            continue
        request_context_ids = {row.artifact_id for row in request_event.context.rows}
        if decision_event.context.target_artifact.artifact_id in request_context_ids:
            return True
        matter_to_decision_context = topic_subject_v3_text_similarity(
            compact_text(request_event.event_payload.get("matter_he")),
            topic_subject_v3_full_event_text(decision_event),
        )
        if matter_to_decision_context >= 0.25:
            return True
    return False


def topic_subject_v3_outcome_quote_norm(event_payload: dict[str, Any]) -> str:
    outcome = event_payload.get("outcome") if isinstance(event_payload.get("outcome"), dict) else {}
    quote = normalize_for_search(compact_text(outcome.get("outcome_quote_he")))
    return quote if len(quote) >= 8 else ""


def topic_subject_v3_contexts_overlap(*, left: TopicSubjectV3EventResult, right: TopicSubjectV3EventResult) -> bool:
    left_rows = {row.artifact_id for row in left.context.rows}
    right_rows = {row.artifact_id for row in right.context.rows}
    if left_rows & right_rows:
        return True
    left_ordinal = left.context.target_artifact.source_ordinal or 0
    right_ordinal = right.context.target_artifact.source_ordinal or 0
    return abs(left_ordinal - right_ordinal) <= 3


def topic_subject_v3_actions_compatible(left_payload: dict[str, Any], right_payload: dict[str, Any]) -> bool:
    left_action = compact_text(left_payload.get("action_type_he"))
    right_action = compact_text(right_payload.get("action_type_he"))
    if not left_action or not right_action:
        return True
    if left_action == right_action:
        return True
    return bool(left_payload.get("outcome_is_decision")) != bool(right_payload.get("outcome_is_decision"))


def topic_subject_v3_shared_evidence_similarity(*, left: TopicSubjectV3EventResult, right: TopicSubjectV3EventResult) -> float:
    left_text = " ".join([compact_text(left.event_payload.get("matter_he")), left.context.target_artifact.real_text, topic_subject_v3_decision_quote(left.event_payload)])
    right_text = " ".join([compact_text(right.event_payload.get("matter_he")), right.context.target_artifact.real_text, topic_subject_v3_decision_quote(right.event_payload)])
    return topic_subject_v3_text_similarity(left_text, right_text)


def topic_subject_v3_target_row_request_like_without_decision(event: TopicSubjectV3EventResult) -> bool:
    return topic_subject_v3_target_text_request_like_without_decision(event.context.target_artifact.real_text)


def topic_subject_v3_target_text_request_like_without_decision(text: str) -> bool:
    text_norm = normalize_for_search(corrected_hebrew_text(text))
    return text_has_any(text_norm, REQUEST_ACTION_CUES) and not text_has_any(text_norm, DECISION_ACTION_CUES + APPROVAL_VERB_CUES + STRONG_APPROVAL_ACTION_CUES)


def topic_subject_v3_has_explicit_decision_evidence(event: TopicSubjectV3EventResult) -> bool:
    evidence_norm = normalize_for_search(" ".join([topic_subject_v3_decision_quote(event.event_payload), compact_text(event.event_payload.get("action_quote_he"))]))
    return text_has_any(evidence_norm, DECISION_ACTION_CUES + APPROVAL_VERB_CUES + STRONG_APPROVAL_ACTION_CUES)


def topic_subject_v3_decision_quote(event_payload: dict[str, Any]) -> str:
    outcome = event_payload.get("outcome") if isinstance(event_payload.get("outcome"), dict) else {}
    return compact_text(outcome.get("outcome_quote_he"))


def topic_subject_v3_text_similarity(left: str, right: str) -> float:
    left_tokens = topic_subject_v3_identity_tokens(left)
    right_tokens = topic_subject_v3_identity_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return overlap / float(min(len(left_tokens), len(right_tokens)))


def topic_subject_v3_identity_tokens(value: str) -> set[str]:
    stop = {
        "של",
        "את",
        "על",
        "עם",
        "או",
        "כל",
        "לפי",
        "דין",
        "כדין",
        "מועצת",
        "העיר",
        "חברי",
        "המועצה",
        "בקשה",
        "בקשת",
        "אישור",
        "מאשרים",
        "פה",
        "אחד",
        "נושא",
        "בנושא",
        "לסדר",
        "דיון",
    }
    tokens: set[str] = set()
    for raw_token in normalize_for_search(value).split():
        if len(raw_token) < 4 or raw_token in stop or re.search(r"\d", raw_token):
            continue
        tokens.add(raw_token)
        normalized_token = topic_subject_v3_identity_token_variant(raw_token)
        if len(normalized_token) >= 4 and normalized_token not in stop:
            tokens.add(normalized_token)
    return tokens


def topic_subject_v3_identity_token_variant(token: str) -> str:
    value = token
    while len(value) > 4 and value[0] in {"ב", "ל", "ו", "ה", "מ", "ש"}:
        value = value[1:]
    while len(value) > 5 and value[-1] in {"ה", "ו", "ם", "ן"}:
        value = value[:-1]
    return value


def topic_subject_v3_secondary_event_role(*, primary: TopicSubjectV3EventResult, secondary: TopicSubjectV3EventResult) -> str:
    primary_decision = bool(primary.event_payload.get("outcome_is_decision"))
    secondary_decision = bool(secondary.event_payload.get("outcome_is_decision"))
    if primary_decision and (not secondary_decision or topic_subject_v3_target_row_request_like_without_decision(secondary)):
        return "supporting_phase"
    return "duplicate_event_prediction"


def topic_subject_v3_event_identity_reason(*, primary: TopicSubjectV3EventResult, secondary: TopicSubjectV3EventResult) -> str:
    primary_quote = topic_subject_v3_outcome_quote_norm(primary.event_payload)
    secondary_quote = topic_subject_v3_outcome_quote_norm(secondary.event_payload)
    if primary_quote and primary_quote == secondary_quote:
        return "same_exact_outcome_quote"
    if topic_subject_v3_secondary_event_role(primary=primary, secondary=secondary) == "supporting_phase":
        return "nearby_request_or_non_decision_phase_shares_event_evidence_with_primary_decision"
    return "same_document_context_and_compatible_action_matter_evidence"


def topic_subject_v3_merged_context_rows(group_events: list[TopicSubjectV3EventResult]) -> list[TopicDecisionArtifact]:
    rows_by_id: dict[str, TopicDecisionArtifact] = {}
    for event in group_events:
        for row in event.context.rows:
            rows_by_id.setdefault(row.artifact_id, row)
    return sorted(rows_by_id.values(), key=lambda row: (row.source_document_version_id, row.source_ordinal, row.artifact_id))


def topic_subject_v3_merged_row_roles(*, group_events: list[TopicSubjectV3EventResult], primary: TopicSubjectV3EventResult) -> list[dict[str, Any]]:
    role_by_artifact: dict[str, dict[str, Any]] = {}
    primary_anchor_id = primary.context.target_artifact.artifact_id
    for event in group_events:
        merged_role = "primary" if event is primary else topic_subject_v3_secondary_event_role(primary=primary, secondary=event)
        rows = event.event_payload.get("row_roles") if isinstance(event.event_payload.get("row_roles"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            artifact_id = compact_text(row.get("artifact_id"))
            if not artifact_id:
                continue
            existing = role_by_artifact.get(artifact_id)
            candidate = dict(row)
            if artifact_id == primary_anchor_id:
                candidate["event_role"] = "primary"
            elif artifact_id == event.context.target_artifact.artifact_id:
                candidate["event_role"] = "supporting" if merged_role == "supporting_phase" else "duplicate"
            elif compact_text(candidate.get("event_role")) == "primary":
                candidate["event_role"] = "supporting"
            role_by_artifact[artifact_id] = topic_subject_v3_preferred_row_role(existing, candidate)
    for row in primary.context.rows:
        role_by_artifact.setdefault(
            row.artifact_id,
            {
                "artifact_id": row.artifact_id,
                "row_role": "action_anchor" if row.artifact_id == primary_anchor_id else "dependent_detail",
                "event_role": "primary" if row.artifact_id == primary_anchor_id else "supporting",
                "reason_he": "merged_context_row",
            },
        )
    return [role_by_artifact[row.artifact_id] for row in primary.context.rows if row.artifact_id in role_by_artifact]


def topic_subject_v3_preferred_row_role(existing: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    if existing is None:
        return candidate
    score = {"primary": 4, "supporting": 3, "supporting_phase": 3, "duplicate": 2, "duplicate_event_prediction": 2, "not_part_of_event": 1, "": 0}
    existing_score = score.get(compact_text(existing.get("event_role")), 0)
    candidate_score = score.get(compact_text(candidate.get("event_role")), 0)
    return candidate if candidate_score > existing_score else existing


def topic_subject_v3_event_rank(event: TopicSubjectV3EventResult) -> tuple[float, float, float, float]:
    status_score = {"accepted": 3.0, "needs_review": 2.0, "failed": 1.0}.get(event.validation_status, 0.0)
    role_score = 1.0 if compact_text(event.event_payload.get("target_row_role")) == "action_anchor" else 0.0
    if bool(event.event_payload.get("outcome_is_decision")) and topic_subject_v3_target_row_request_like_without_decision(event):
        role_score -= 0.75
    if bool(event.event_payload.get("outcome_is_decision")) and topic_subject_v3_has_explicit_decision_evidence(event):
        role_score += 0.35
    confidence = clamp_float(event.event_payload.get("confidence"), default=0.0)
    source_order = -float(event.context.target_artifact.source_ordinal or 0)
    return status_score, role_score, confidence, source_order


def persist_topic_subject_v3_result(
    *,
    session: Session,
    run: TopicSubjectV3Run,
    events: list[TopicSubjectV3EventResult],
    row_quality_rows: list[TopicSubjectV3RowQualityData],
) -> None:
    for event in events:
        payload = event.event_payload
        outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
        artifact = event.context.target_artifact
        source_ordinals = [row.source_ordinal for row in event.context.rows if row.source_ordinal is not None]
        source_pages = [page for row in event.context.rows for page in (row.start_page, row.end_page) if page is not None]
        row = TopicSubjectV3Event(
            run_id=int(run.id),
            municipality_slug=str(run.municipality_slug),
            event_id=event.event_id,
            event_index=event.event_index,
            source_document_id=artifact.source_document_id,
            source_document_version_id=artifact.source_document_version_id,
            anchor_artifact_id=artifact.artifact_id,
            anchor_semantic_node_id=artifact.semantic_node_id,
            source_ordinal_start=min(source_ordinals) if source_ordinals else artifact.source_ordinal,
            source_ordinal_end=max(source_ordinals) if source_ordinals else artifact.source_ordinal,
            source_page_start=min(source_pages) if source_pages else artifact.start_page,
            source_page_end=max(source_pages) if source_pages else artifact.end_page,
            action_type_he=str(payload.get("action_type_he") or ""),
            action_type_norm=str(payload.get("action_type_norm") or ""),
            action_subtype_he=string_or_none(payload.get("action_subtype_he")),
            action_subtype_norm=string_or_none(payload.get("action_subtype_norm")),
            other_action_type_he=string_or_none(payload.get("other_action_type_he")),
            other_action_type_norm=string_or_none(payload.get("other_action_type_norm")),
            action_type_confidence=float(payload.get("action_type_confidence") or 0.0),
            action_type_status=str(payload.get("action_type_status") or "unknown"),
            matter_he=str(payload.get("matter_he") or ""),
            matter_norm=str(payload.get("matter_norm") or ""),
            action_details_he=string_or_none(payload.get("action_details_he")),
            action_quote_he=string_or_none(payload.get("action_quote_he")),
            subject_summary_he=string_or_none(payload.get("subject_summary_he")),
            what_text_is_about_he=string_or_none(payload.get("what_text_is_about_he")),
            outcome_is_decision=bool(payload.get("outcome_is_decision")),
            outcome_label_he=string_or_none(outcome.get("outcome_label_he")),
            outcome_label_norm=string_or_none(outcome.get("outcome_label_norm")),
            outcome_summary_he=string_or_none(outcome.get("outcome_summary_he")),
            outcome_quote_he=string_or_none(outcome.get("outcome_quote_he")),
            confidence=float(payload.get("confidence") or 0.0),
            validation_status=event.validation_status,
            failure_reason="; ".join(event.failure_reasons),
            normalized_event_json=json.dumps(compact_payload_for_prompt(event.normalized_event), ensure_ascii=False),
            extraction_payload_json=json.dumps(compact_payload_for_prompt(event.extraction_payload), ensure_ascii=False),
            judge_payload_json=json.dumps(compact_payload_for_prompt(event.judge_payload), ensure_ascii=False),
            evidence_refs_json=json.dumps(topic_subject_v3_evidence_refs(event), ensure_ascii=False),
            metadata_json=json.dumps(
                {
                    "provenance": PROVENANCE_V3,
                    "context_id": event.context.context_id,
                    "context_artifact_ids": [row.artifact_id for row in event.context.rows],
                    "source_topic_hidden_from_extraction": True,
                },
                ensure_ascii=False,
            ),
        )
        session.add(row)

    for row_quality in row_quality_rows:
        artifact = row_quality.artifact
        session.add(
            TopicSubjectV3RowQuality(
                run_id=int(run.id),
                municipality_slug=str(run.municipality_slug),
                event_id=row_quality.event_id or None,
                artifact_id=artifact.artifact_id,
                semantic_node_id=artifact.semantic_node_id,
                source_document_id=artifact.source_document_id,
                source_document_version_id=artifact.source_document_version_id,
                source_ordinal=artifact.source_ordinal,
                source_page_start=artifact.start_page,
                source_page_end=artifact.end_page,
                source_kind=artifact.source_kind,
                source_title=artifact.source_title,
                source_topic_label_he=artifact.topic_label_he,
                real_text=artifact.real_text,
                corrected_text_he=corrected_hebrew_text(artifact.real_text),
                row_role=row_quality.row_role,
                event_role=row_quality.event_role,
                topic_relevance=row_quality.topic_relevance,
                action_type_by_dicta=row_quality.action_type_by_dicta or None,
                action_subtype_by_dicta=row_quality.action_subtype_by_dicta or None,
                other_action_type_by_dicta=row_quality.other_action_type_by_dicta or None,
                matter_by_dicta=row_quality.matter_by_dicta or None,
                action_details_by_dicta=row_quality.action_details_by_dicta or None,
                outcome_by_dicta=row_quality.outcome_by_dicta or None,
                model_prediction_json=json.dumps(row_quality.model_prediction, ensure_ascii=False),
                judge_prediction_json=json.dumps(row_quality.judge_prediction, ensure_ascii=False),
                prediction_comparison=row_quality.prediction_comparison or None,
                judge_status=row_quality.judge_status,
                ground_truth_he=row_quality.ground_truth_he,
                reason_for_failure=row_quality.reason_for_failure or None,
                quality_status=row_quality.quality_status,
                metadata_json=json.dumps(row_quality.metadata, ensure_ascii=False),
            )
        )
    session.flush()


def topic_subject_v3_evidence_refs(event: TopicSubjectV3EventResult) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    action_quote = compact_text(event.event_payload.get("action_quote_he"))
    if action_quote:
        refs.append({"kind": "action", "quote_he": action_quote})
    outcome = event.event_payload.get("outcome") if isinstance(event.event_payload.get("outcome"), dict) else {}
    outcome_quote = compact_text(outcome.get("outcome_quote_he"))
    if outcome_quote:
        refs.append({"kind": "outcome", "quote_he": outcome_quote})
    return refs


def topic_subject_v3_event_source_rows(event: TopicSubjectV3EventResult) -> list[dict[str, Any]]:
    row_roles = event.event_payload.get("row_roles") if isinstance(event.event_payload.get("row_roles"), list) else []
    role_by_artifact = {
        str(row.get("artifact_id") or ""): row
        for row in row_roles
        if isinstance(row, dict)
    }
    return [
        {
            "artifact_id": artifact.artifact_id,
            "semantic_node_id": artifact.semantic_node_id,
            "source_ordinal": artifact.source_ordinal,
            "page_span": {"start": artifact.start_page, "end": artifact.end_page},
            "row_role": compact_text(role_by_artifact.get(artifact.artifact_id, {}).get("row_role")) or ("anchor" if artifact.artifact_id == event.context.target_artifact.artifact_id else "context"),
            "event_role": compact_text(role_by_artifact.get(artifact.artifact_id, {}).get("event_role")),
            "role_reason_he": compact_text(role_by_artifact.get(artifact.artifact_id, {}).get("reason_he") or role_by_artifact.get(artifact.artifact_id, {}).get("role_reason_he")),
            "source_topic_label_he": artifact.topic_label_he,
            "full_source_text_he": artifact.real_text,
            "corrected_text_he": corrected_hebrew_text(artifact.real_text),
        }
        for artifact in event.context.rows
    ]


def topic_subject_v3_full_event_text(event: TopicSubjectV3EventResult) -> str:
    return "\n\n".join(f"[{row.source_ordinal}] {row.real_text}" for row in event.context.rows)


def write_topic_subject_v3_outputs(*, output_dir: Path, result: TopicSubjectV3ResearchResult) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    events_json_path = output_dir / "v3_events.json"
    quality_json_path = output_dir / "v3_quality_report.json"
    quality_md_path = output_dir / "v3_quality_report.md"
    summary_json_path = output_dir / "v3_summary.json"
    events_json_path.write_text(json.dumps([topic_subject_v3_event_to_dict(event) for event in result.events], ensure_ascii=False, indent=2), encoding="utf-8")
    quality_json_path.write_text(json.dumps([topic_subject_v3_row_quality_to_dict(row) for row in result.row_quality_rows], ensure_ascii=False, indent=2), encoding="utf-8")
    quality_md_path.write_text(topic_subject_v3_quality_report_markdown(result.row_quality_rows), encoding="utf-8")
    summary_json_path.write_text(
        json.dumps(
            {
                "run_id": result.run_id,
                "selected_artifacts": len(result.artifacts),
                "events": result.event_count,
                "candidate_subjects": result.candidate_subject_count,
                "candidate_decisions": result.candidate_decision_count,
                "failed_rows": result.failed_count,
                "elapsed_seconds": result.elapsed_seconds,
                "provenance": PROVENANCE_V3,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "v3_events_json": str(events_json_path),
        "v3_quality_report_json": str(quality_json_path),
        "v3_quality_report_md": str(quality_md_path),
        "v3_summary_json": str(summary_json_path),
    }


def topic_subject_v3_event_to_dict(event: TopicSubjectV3EventResult) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_index": event.event_index,
        "artifact_id": event.context.target_artifact.artifact_id,
        "anchor_source_text_he": event.context.target_artifact.real_text,
        "full_source_text_he": topic_subject_v3_full_event_text(event),
        "event_source_rows": topic_subject_v3_event_source_rows(event),
        "source_document_version_id": event.context.target_artifact.source_document_version_id,
        "source_ordinal": event.context.target_artifact.source_ordinal,
        "validation_status": event.validation_status,
        "failure_reasons": event.failure_reasons,
        "event_payload": event.event_payload,
        "normalized_event": compact_payload_for_prompt(event.normalized_event),
        "judge_payload": compact_payload_for_prompt(event.judge_payload),
    }


def topic_subject_v3_row_quality_to_dict(row: TopicSubjectV3RowQualityData) -> dict[str, Any]:
    return {
        "artifact_id": row.artifact.artifact_id,
        "semantic_node_id": row.artifact.semantic_node_id,
        "event_id": row.event_id,
        "source_topic_label_he": row.artifact.topic_label_he,
        "full_source_text_he": row.artifact.real_text,
        "corrected_text_he": corrected_hebrew_text(row.artifact.real_text),
        "row_role": row.row_role,
        "event_role": row.event_role,
        "topic_relevance": row.topic_relevance,
        "model_prediction": {
            "action_type_he": row.action_type_by_dicta,
            "action_subtype_he": row.action_subtype_by_dicta,
            "other_action_type_he": row.other_action_type_by_dicta,
            "matter_he": row.matter_by_dicta,
            "action_details_he": row.action_details_by_dicta,
            "outcome_he": row.outcome_by_dicta,
        },
        "judge_prediction": row.judge_prediction,
        "prediction_comparison": row.prediction_comparison,
        "ground_truth_he": row.ground_truth_he,
        "judge_status": row.judge_status,
        "reason_for_failure": row.reason_for_failure,
        "quality_status": row.quality_status,
        "metadata": row.metadata,
    }


def topic_subject_v3_quality_report_markdown(rows: list[TopicSubjectV3RowQualityData]) -> str:
    problem_rows = [
        row
        for row in rows
        if topic_subject_v3_row_needs_quality_report(row)
    ]
    lines = [
        "## V3 Quality Report",
        "",
        "Focused on problematic, low-confidence, or conservative `אחר` predictions.",
        "",
    ]
    if not problem_rows:
        return "\n".join([*lines, "No problematic or low-confidence rows found.", ""]) + "\n"
    lines.extend([
        "| Source/Text | Model Prediction | Judge Prediction | Reason | Status |",
        "|---|---|---|---|---|",
    ])
    for row in problem_rows[:80]:
        model_prediction = topic_subject_v3_model_prediction_label(row)
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_table(shorten(row.artifact.real_text, 180)),
                    escape_table(model_prediction),
                    escape_table(shorten(topic_subject_v3_judge_prediction_label(row), 180)),
                    escape_table(shorten(row.reason_for_failure, 180)),
                    escape_table(row.quality_status),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def topic_subject_v3_row_needs_quality_report(row: TopicSubjectV3RowQualityData) -> bool:
    if row.quality_status in {"model_error", "failed", "needs_review"}:
        return True
    if "approval_action_without_decision_outcome" in row.reason_for_failure:
        return True
    if row.quality_status in {"accepted_primary", "accepted_duplicate", "accepted_supporting_phase"}:
        return row.action_type_by_dicta == "אחר" or (
            isinstance(row.model_prediction, dict)
            and str(row.model_prediction.get("action_type_status") or row.model_prediction.get("action_label_status") or "") in {"other_low_confidence", "other_uncontrolled_label"}
        )
    if row.action_type_by_dicta == "אחר":
        return True
    if row.prediction_comparison in {"partially_different", "different", "model_invalid"}:
        return True
    return bool(
        isinstance(row.model_prediction, dict)
        and str(row.model_prediction.get("action_type_status") or row.model_prediction.get("action_label_status") or "") in {"other_low_confidence", "other_uncontrolled_label"}
    )


def topic_subject_v3_model_prediction_label(row: TopicSubjectV3RowQualityData) -> str:
    action = row.action_type_by_dicta
    if action == "אחר" and row.other_action_type_by_dicta:
        action = f"אחר ({row.other_action_type_by_dicta})"
    if row.action_subtype_by_dicta:
        action = f"{action} / {row.action_subtype_by_dicta}" if action else row.action_subtype_by_dicta
    matter = f"על {row.matter_by_dicta}" if row.matter_by_dicta else ""
    outcome = f" תוצאה: {row.outcome_by_dicta}" if row.outcome_by_dicta else ""
    return compact_text(f"{action} {matter}{outcome}")


def topic_subject_v3_judge_prediction_label(row: TopicSubjectV3RowQualityData) -> str:
    prediction = row.judge_prediction or {}
    action = compact_text(prediction.get("action_type_he"))
    other = compact_text(prediction.get("other_action_type_he"))
    if action == "אחר" and other:
        action = f"אחר ({other})"
    subtype = compact_text(prediction.get("action_subtype_he"))
    if subtype:
        action = f"{action} / {subtype}" if action else subtype
    matter = compact_text(prediction.get("matter_he"))
    outcome = compact_text(prediction.get("outcome_label_he") or prediction.get("outcome_type"))
    return compact_text(f"{action} על {matter} תוצאה: {outcome}") or row.ground_truth_he


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
            metadata_json=json.dumps(
                {
                    "provenance": PROVENANCE,
                    "offset": config.offset,
                    "limit": config.limit,
                    "small_model_name": config.small_model_name,
                    "heavy_model_stages": sorted(TOPIC_SUBJECT_HEAVY_MODEL_STAGES),
                    "small_model_stages": sorted(TOPIC_SUBJECT_SMALL_MODEL_STAGES),
                },
                ensure_ascii=False,
            ),
        )
        session.add(run_row)
        session.flush()

    event_blocks = build_topic_subject_event_blocks(artifacts)
    extracted, quality_rows = extract_subjects_from_event_blocks(event_blocks=event_blocks, client=client, config=config)

    apply_event_grouping(artifacts=artifacts, extracted=extracted, quality_rows=quality_rows)
    apply_event_block_links(event_blocks=event_blocks, extracted=extracted, quality_rows=quality_rows)

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
        event_blocks=[event_block_to_dict(block) for block in event_blocks],
    )
    if config.output_dir is not None:
        result.output_paths.update(write_topic_subject_outputs(output_dir=config.output_dir, result=result))
    return result


def build_topic_subject_event_blocks(artifacts: list[TopicDecisionArtifact]) -> list[TopicSubjectEventBlock]:
    blocks: list[TopicSubjectEventBlock] = []
    artifacts_by_docver: dict[int, list[TopicDecisionArtifact]] = {}
    local_payloads = {artifact.artifact_id: preclassify_trivial_subject_payload(artifact) for artifact in artifacts}
    for artifact in artifacts:
        artifacts_by_docver.setdefault(artifact.source_document_version_id, []).append(artifact)

    for doc_artifacts in artifacts_by_docver.values():
        doc_artifacts.sort(key=lambda item: (item.source_ordinal, item.artifact_id))
        pending_details: list[TopicDecisionArtifact] = []
        index = 0
        while index < len(doc_artifacts):
            artifact = doc_artifacts[index]
            local_payload = local_payloads.get(artifact.artifact_id)
            if local_payload is not None:
                blocks.extend(context_blocks_for_pending(pending_details))
                pending_details = []
                kind = "local_subject" if bool(local_payload.get("agenda_title_subject")) else "local_non_subject"
                blocks.append(
                    TopicSubjectEventBlock(
                        block_id=event_block_id([artifact]),
                        kind=kind,
                        artifacts=[artifact],
                        anchor_artifact=artifact if kind == "local_subject" else None,
                        local_payload_by_artifact_id={artifact.artifact_id: local_payload},
                    )
                )
                index += 1
                continue

            if is_model_action_anchor_candidate(artifact):
                previous_details = pending_details[-2:]
                blocks.extend(context_blocks_for_pending(pending_details[:-2]))
                block_artifacts = [*previous_details, artifact]
                pending_details = []
                index += 1
                while index < len(doc_artifacts) and len(block_artifacts) < 5:
                    next_artifact = doc_artifacts[index]
                    if local_payloads.get(next_artifact.artifact_id) is not None:
                        break
                    follows_current_block = is_following_block_detail_candidate(anchor=artifact, detail=next_artifact)
                    if is_model_action_anchor_candidate(next_artifact) and not follows_current_block:
                        break
                    if not follows_current_block:
                        break
                    block_artifacts.append(next_artifact)
                    index += 1
                blocks.append(
                    TopicSubjectEventBlock(
                        block_id=event_block_id(block_artifacts),
                        kind="model_block",
                        artifacts=block_artifacts,
                        anchor_artifact=artifact,
                    )
                )
                continue

            pending_details.append(artifact)
            index += 1
        blocks.extend(context_blocks_for_pending(pending_details))
    return blocks


def context_blocks_for_pending(artifacts: list[TopicDecisionArtifact]) -> list[TopicSubjectEventBlock]:
    return [
        TopicSubjectEventBlock(
            block_id=event_block_id([artifact]),
            kind="context_block",
            artifacts=[artifact],
            anchor_artifact=None,
        )
        for artifact in artifacts
    ]


def is_model_action_anchor_candidate(artifact: TopicDecisionArtifact) -> bool:
    text_norm = normalize_for_search(corrected_hebrew_text(artifact.real_text))
    has_decision_action = text_has_any(text_norm, DECISION_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES)
    if protocol_or_meeting_header_role(text_norm):
        return False
    if protocol_header_shape_for_linking(text_norm) and not has_decision_action:
        return False
    if has_weak_title_fragment_shape(text_norm):
        return False
    if is_condition_scope_text(text_norm) and not text_has_any(text_norm, REQUEST_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES):
        return False
    return has_dialogue_action_evidence(text_norm)


def is_following_block_detail_candidate(*, anchor: TopicDecisionArtifact, detail: TopicDecisionArtifact) -> bool:
    distance = abs(detail.source_ordinal - anchor.source_ordinal)
    if distance < 1 or distance > 3:
        return False
    text_norm = normalize_for_search(corrected_hebrew_text(detail.real_text))
    if protocol_or_meeting_header_role(text_norm) or protocol_header_shape_for_linking(text_norm):
        return False
    if text_norm.startswith("סעיף"):
        return False
    if has_weak_title_fragment_shape(text_norm):
        return False
    anchor_norm = normalize_for_search(corrected_hebrew_text(anchor.real_text))
    if is_condition_scope_text(text_norm):
        return True
    if has_question_detail_shape(text_norm) and has_inquiry_request_shape(anchor_norm):
        return True
    if has_enumerated_proposal_detail_shape(text_norm) and anchor_allows_numbered_detail_continuation(anchor_norm):
        return True
    if text_has_any(text_norm, DECISION_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES) and not has_enumerated_proposal_detail_shape(text_norm):
        return False
    if has_continuation_detail_shape(text_norm) or has_detail_without_action_shape(detail.real_text):
        return True
    return bool(compact_text(anchor.topic_label_he) == compact_text(detail.topic_label_he) and shared_context_token_count(anchor.real_text, detail.real_text) >= 1)


def anchor_allows_numbered_detail_continuation(anchor_norm: str) -> bool:
    return text_has_any(anchor_norm, AGENDA_PROPOSAL_CUES + REPORT_ACTION_CUES + ("הצעה לסדר", "הצעה לאישורכם", "מסכם את התקופה"))


def extract_subjects_from_event_blocks(
    *,
    event_blocks: list[TopicSubjectEventBlock],
    client: TopicSubjectClient,
    config: TopicSubjectResearchConfig,
) -> tuple[list[ExtractedTopicSubject], list[TopicSubjectQualityRow]]:
    extracted: list[ExtractedTopicSubject] = []
    quality_by_artifact: dict[str, TopicSubjectQualityRow] = {}
    for block in event_blocks:
        if block.kind in {"local_non_subject", "local_subject"}:
            for artifact in block.artifacts:
                payload = block.local_payload_by_artifact_id.get(artifact.artifact_id) or background_context_payload(artifact=artifact, block=block)
                artifact_subjects, quality = process_topic_subject_payload(artifact=artifact, model_payload=payload)
                add_block_metadata(quality=quality, block=block)
                extracted.extend(artifact_subjects)
                quality_by_artifact[artifact.artifact_id] = quality
            continue
        if block.kind == "context_block" or block.anchor_artifact is None:
            for artifact in block.artifacts:
                artifact_subjects, quality = process_topic_subject_payload(artifact=artifact, model_payload=background_context_payload(artifact=artifact, block=block))
                add_block_metadata(quality=quality, block=block)
                extracted.extend(artifact_subjects)
                quality_by_artifact[artifact.artifact_id] = quality
            continue

        anchor = block.anchor_artifact
        block_prompt_artifact = event_block_prompt_artifact(block)
        model_payload = client.extract(artifact=block_prompt_artifact, config=config)
        if model_payload_without_subjects(model_payload):
            model_payload = decision_anchor_fallback_payload(artifact=anchor, original_payload=model_payload) or model_payload
        anchor_subjects, anchor_quality = process_topic_subject_payload(artifact=anchor, model_payload=model_payload)
        add_block_metadata(quality=anchor_quality, block=block)
        extracted.extend(anchor_subjects)
        quality_by_artifact[anchor.artifact_id] = anchor_quality
        for artifact in block.artifacts:
            if artifact.artifact_id == anchor.artifact_id:
                continue
            artifact_subjects, quality = process_topic_subject_payload(artifact=artifact, model_payload=background_context_payload(artifact=artifact, block=block))
            add_block_metadata(quality=quality, block=block)
            extracted.extend(artifact_subjects)
            quality_by_artifact[artifact.artifact_id] = quality
    return extracted, [quality_by_artifact[artifact.artifact_id] for block in event_blocks for artifact in block.artifacts if artifact.artifact_id in quality_by_artifact]


def event_block_prompt_artifact(block: TopicSubjectEventBlock) -> TopicDecisionArtifact:
    anchor = block.anchor_artifact or block.artifacts[0]
    block_rows = [event_block_row_dict(artifact=artifact, role="anchor" if artifact.artifact_id == anchor.artifact_id else "context") for artifact in block.artifacts]
    return TopicDecisionArtifact(
        artifact_id=anchor.artifact_id,
        semantic_node_id=anchor.semantic_node_id,
        topic_label_he=anchor.topic_label_he,
        root_topic_id=anchor.root_topic_id,
        root_label_he=anchor.root_label_he,
        child_topic_id=anchor.child_topic_id,
        child_label_he=anchor.child_label_he,
        source_kind=anchor.source_kind,
        source_document_id=anchor.source_document_id,
        source_document_version_id=anchor.source_document_version_id,
        source_ordinal=anchor.source_ordinal,
        source_title=anchor.source_title,
        source_url=anchor.source_url,
        artifact_kind=anchor.artifact_kind,
        start_page=anchor.start_page,
        end_page=anchor.end_page,
        header_path=anchor.header_path,
        real_text=anchor.real_text,
        retrieval_text=anchor.retrieval_text,
        topic_confidence=anchor.topic_confidence,
        existing_decision_candidate_id=anchor.existing_decision_candidate_id,
        metadata={**anchor.metadata, "topic_subject_event_block_id": block.block_id, "event_block_rows": block_rows},
        decision_context_text=event_block_context_text(block),
        neighbor_contexts=anchor.neighbor_contexts,
    )


def background_context_payload(*, artifact: TopicDecisionArtifact, block: TopicSubjectEventBlock) -> dict[str, Any]:
    return {
        "subjects": [],
        "overall_summary_he": "הקטע נשמר כחלק מהקשר/פרטי בלוק אירוע, ואינו נשלח כיחידת נושא עצמאית.",
        "rationale_he": "local_event_block_builder",
        "preclassified": True,
        "event_block_context": True,
        "event_block_id": block.block_id,
    }


def model_payload_without_subjects(payload: dict[str, Any]) -> bool:
    if payload.get("error_code"):
        return False
    return not isinstance(payload.get("subjects"), list) or len(payload.get("subjects") or []) == 0


def decision_anchor_fallback_payload(*, artifact: TopicDecisionArtifact, original_payload: dict[str, Any]) -> dict[str, Any] | None:
    text = corrected_hebrew_text(artifact.real_text)
    text_norm = normalize_for_search(text)
    if protocol_or_meeting_header_role(text_norm):
        return None
    if not text_has_any(text_norm, DECISION_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES):
        return None
    if text_has_any(text_norm, REQUEST_ACTION_CUES) and not text_has_any(text_norm, APPROVAL_VERB_CUES + STRONG_APPROVAL_ACTION_CUES):
        return None
    root = "אישור פרוטוקול" if looks_like_protocol_approval(text_norm) else "אישור החלטה"
    evidence = grounded_subject_type_evidence(root=root, raw_text=artifact.real_text)
    if not evidence:
        return None
    about = f"זוהה עוגן החלטה מפורש בטקסט הגולמי בנושא {artifact.topic_label_he}."
    return {
        "subjects": [
            {
                "subject_root_label_he": root,
                "subject_child_label_he": "",
                "subject_object_he": compact_text(artifact.topic_label_he),
                "subject_details_he": text[:500],
                "subject_type_evidence_he": evidence,
                "artifact_role": "substantive_subject",
                "topic_relevance": "topic_bearing",
                "subject_summary_he": about,
                "what_text_is_about_he": about,
                "is_decision": True,
                "decision": {
                    "decision_label_he": root,
                    "decision_summary_he": about,
                    "source_quote_he": evidence,
                    "confidence": 0.72,
                    "limitations": ["deterministic_fallback_after_empty_model_subjects"],
                },
                "confidence": 0.72,
                "rationale_he": "deterministic_fallback_after_empty_model_subjects",
            }
        ],
        "overall_summary_he": about,
        "rationale_he": "deterministic_fallback_after_empty_model_subjects",
        "fallback_applied": True,
        "fallback_original_payload": compact_subject_payload(original_payload),
    }


def add_block_metadata(*, quality: TopicSubjectQualityRow, block: TopicSubjectEventBlock) -> None:
    quality.metadata = {
        **quality.metadata,
        "event_block_id": block.block_id,
        "event_block_kind": block.kind,
        "event_block_artifact_ids": [artifact.artifact_id for artifact in block.artifacts],
        "event_block_anchor_artifact_id": block.anchor_artifact.artifact_id if block.anchor_artifact is not None else None,
    }


def apply_event_block_links(*, event_blocks: list[TopicSubjectEventBlock], extracted: list[ExtractedTopicSubject], quality_rows: list[TopicSubjectQualityRow]) -> None:
    quality_by_artifact = {row.artifact_id: row for row in quality_rows}
    for block in event_blocks:
        if block.kind != "model_block" or block.anchor_artifact is None:
            continue
        anchor_quality = quality_by_artifact.get(block.anchor_artifact.artifact_id)
        if anchor_quality is None or anchor_quality.anchor_status != "validated_anchor":
            continue
        for artifact in block.artifacts:
            if artifact.artifact_id == block.anchor_artifact.artifact_id:
                continue
            quality = quality_by_artifact.get(artifact.artifact_id)
            if quality is None or quality.row_role in {"document_fragment", "vote_metadata"}:
                continue
            quality.event_id = anchor_quality.event_id
            quality.event_topic = anchor_quality.event_topic
            quality.row_role = "dependent_detail"
            quality.anchor_status = "linked_to_validated_anchor"
            quality.linked_event_id = anchor_quality.event_id
            quality.link_confidence = 0.78
            quality.link_reason = "event_block_builder"
            quality.topic_relevance = "topic_bearing"
            quality.artifact_role = "event_block_detail"
            quality.subject_root_by_dicta = anchor_quality.subject_root_by_dicta
            quality.subject_child_by_dicta = anchor_quality.subject_child_by_dicta
            quality.subject_object_by_dicta = anchor_quality.subject_object_by_dicta or quality.subject_object_by_dicta
            quality.action_root_by_dicta = anchor_quality.action_root_by_dicta or anchor_quality.subject_root_by_dicta
            quality.action_child_by_dicta = anchor_quality.action_child_by_dicta or anchor_quality.subject_child_by_dicta
            quality.subject_matter_by_dicta = anchor_quality.subject_matter_by_dicta or anchor_quality.subject_object_by_dicta or quality.subject_object_by_dicta
            quality.action_details_by_dicta = anchor_quality.action_details_by_dicta or anchor_quality.subject_details_by_dicta
            quality.my_judgment = "linked_dependent_detail"
            quality.ground_truth = f"הקטע אינו נושא עצמאי; הוא חלק מבלוק אירוע סמוך מסוג {subject_label_display(quality.subject_root_by_dicta, quality.subject_child_by_dicta)}."
            quality.reason_for_failure = ""
            quality.status = "linked_detail"


def event_block_id(artifacts: list[TopicDecisionArtifact]) -> str:
    digest = hashlib.sha1("|".join(f"{artifact.source_document_version_id}:{artifact.artifact_id}" for artifact in artifacts).encode("utf-8")).hexdigest()[:16]
    return f"topic_subject_block_{digest}"


def event_block_context_text(block: TopicSubjectEventBlock) -> str:
    return "\n".join(f"[{index + 1}] {artifact.real_text}" for index, artifact in enumerate(block.artifacts))


def event_block_row_dict(*, artifact: TopicDecisionArtifact, role: str) -> dict[str, Any]:
    text_norm = normalize_for_search(corrected_hebrew_text(artifact.real_text))
    return {
        "artifact_id": artifact.artifact_id,
        "source_ordinal": artifact.source_ordinal,
        "role": role,
        "topic_label_he": artifact.topic_label_he,
        "raw_text": artifact.real_text,
        "corrected_text": corrected_hebrew_text(artifact.real_text),
        "local_row_role_hint": local_row_role_hint(text_norm),
    }


def local_row_role_hint(text_norm: str) -> str:
    if is_agenda_marker_text(text_norm):
        return "agenda_marker"
    if has_clear_agenda_request_title_shape(text_norm):
        return "agenda_request_title"
    if has_weak_title_fragment_shape(text_norm):
        return "title_fragment"
    if has_vote_detail_shape(text_norm):
        return "vote_metadata"
    if has_dialogue_action_evidence(text_norm):
        return "action_anchor"
    if has_detail_without_action_shape(text_norm) or has_continuation_detail_shape(text_norm):
        return "background_detail"
    return "unknown"


def event_block_to_dict(block: TopicSubjectEventBlock) -> dict[str, Any]:
    return {
        "block_id": block.block_id,
        "kind": block.kind,
        "anchor_artifact_id": block.anchor_artifact.artifact_id if block.anchor_artifact is not None else None,
        "artifact_ids": [artifact.artifact_id for artifact in block.artifacts],
        "rows": [event_block_row_dict(artifact=artifact, role="anchor" if block.anchor_artifact is not None and artifact.artifact_id == block.anchor_artifact.artifact_id else "context") for artifact in block.artifacts],
    }


def process_topic_subject_payload(*, artifact: TopicDecisionArtifact, model_payload: dict[str, Any]) -> tuple[list[ExtractedTopicSubject], TopicSubjectQualityRow]:
    if model_payload.get("error_code"):
        quality = TopicSubjectQualityRow(
            artifact_id=artifact.artifact_id,
            semantic_node_id=artifact.semantic_node_id,
            topic=artifact.topic_label_he,
            real_text=artifact.real_text,
            corrected_text=corrected_text_for_report(artifact=artifact, model_payload=model_payload),
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
    if is_attachment_reference_only_text(text_norm):
        return non_subject_payload(artifact=artifact, artifact_role="attachment_reference", about="הארטיפקט מכיל הפניה לנספח/מצורף בלבד, ללא נושא עצמאי.")
    if is_agenda_marker_text(text_norm):
        return non_subject_payload(artifact=artifact, artifact_role="agenda_marker", about="הארטיפקט הוא כותרת או סימון סדר יום בלבד, ללא נושא עצמאי.")
    header_role = protocol_or_meeting_header_role(text_norm)
    if header_role:
        return non_subject_payload(artifact=artifact, artifact_role=header_role, about="הארטיפקט מכיל כותרת פרוטוקול/ישיבה או רשימת משתתפים בלבד, ללא נושא עירוני עצמאי.")
    if protocol_header_shape_for_linking(text_norm) and not text_has_any(text_norm, DECISION_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES):
        return non_subject_payload(artifact=artifact, artifact_role="protocol_header", about="הארטיפקט מכיל כותרת פרוטוקול/ועדה ללא פעולה עירונית עצמאית.")
    if is_contact_info_text(text_norm):
        return non_subject_payload(artifact=artifact, artifact_role="contact_info", about="הארטיפקט מכיל פרטי קשר וכתובת בלבד, ללא נושא עירוני עצמאי.")
    if is_signature_or_footer_text(text_norm):
        return non_subject_payload(artifact=artifact, artifact_role="signature_footer", about="הארטיפקט הוא חתימה או סיום מכתב, ללא בקשה או החלטה מהותית.")
    if has_clear_agenda_request_title_shape(text_norm):
        return agenda_request_title_payload(artifact=artifact)
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


def agenda_request_title_payload(*, artifact: TopicDecisionArtifact) -> dict[str, Any]:
    text = corrected_hebrew_text(artifact.real_text)
    text_norm = normalize_for_search(text)
    is_proposal = text_has_any(text_norm, AGENDA_PROPOSAL_CUES)
    root = "הצעה לסדר יום" if is_proposal else "בקשה"
    child = "" if is_proposal else "בקשת דיון"
    evidence = first_matching_cue(artifact.real_text, AGENDA_PROPOSAL_CUES if is_proposal else AGENDA_REQUEST_TITLE_CUES)
    about = f"הארטיפקט הוא כותרת סעיף סדר יום שמציגה {'הצעה לסדר יום' if is_proposal else 'בקשה לדיון'} בנושא {artifact.topic_label_he}."
    return {
        "corrected_text_he": text,
        "subjects": [
            {
                "subject_root_label_he": root,
                "subject_child_label_he": child,
                "subject_object_he": compact_text(artifact.topic_label_he),
                "subject_details_he": text[:500],
                "subject_type_evidence_he": evidence,
                "artifact_role": "agenda_item_title",
                "topic_relevance": "topic_bearing",
                "subject_summary_he": about,
                "what_text_is_about_he": about,
                "is_decision": False,
                "decision": None,
                "confidence": 0.78,
                "rationale_he": "local_agenda_title_classifier",
            }
        ],
        "overall_summary_he": about,
        "rationale_he": "local_agenda_title_classifier",
        "preclassified": True,
        "agenda_title_subject": True,
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


def is_attachment_reference_only_text(text_norm: str) -> bool:
    if len(text_norm) > 140:
        return False
    if text_has_any(text_norm, REQUEST_ONLY_CUES + DECISION_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES):
        return False
    compact_no_space = re.sub(r"\s+", "", text_norm)
    has_attachment_ref = any(token in compact_no_space for token in ("מצל", "מצ\"ל", "מצ׳ל"))
    if not has_attachment_ref:
        return False
    letter_count = len(re.findall(r"[א-תA-Za-z]", text_norm))
    digit_count = len(re.findall(r"\d", text_norm))
    if digit_count < 1:
        return False
    if letter_count <= 18:
        return True
    return bool(re.match(r"^\s*(?:סעיף\s*)?\d+[\s.)\-:]+", text_norm) and letter_count <= 90)


def is_agenda_marker_text(text_norm: str) -> bool:
    if len(text_norm) > 90:
        return False
    if text_has_any(text_norm, REQUEST_ONLY_CUES + STRONG_APPROVAL_ACTION_CUES):
        return False
    compact_no_space = re.sub(r"\s+", "", text_norm)
    compact_no_punct = re.sub(r"[^א-תA-Za-z0-9]+", "", text_norm)
    agenda_markers = ("עלסדרהיום", "הנושאיםלדיון", "סדרישיבתהמועצה", "הצעותלסדר")
    if not any(marker in compact_no_space or marker in compact_no_punct for marker in agenda_markers):
        return False
    substantive_words = [token for token in text_norm.split() if len(token) >= 4 and token not in {"סעיף", "סדר", "היום", "הצעות", "לסדר", "דיון", "המועצה"}]
    return len(substantive_words) <= 1


def has_clear_agenda_request_title_shape(text_norm: str) -> bool:
    if not text_norm or len(text_norm) > 280:
        return False
    if text_has_any(text_norm, DECISION_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES + RECOMMENDATION_ACTION_CUES + REPORT_ACTION_CUES + DIRECTIVE_ACTION_CUES):
        return False
    if text_has_any(text_norm, AGENDA_PROPOSAL_CUES):
        return not is_agenda_marker_text(text_norm)
    if not text_has_any(text_norm, AGENDA_REQUEST_TITLE_CUES):
        return False
    starts_like_agenda_item = bool(re.match(r"^\s*(?:סעיף\s*)?\d+[).:\-\s]+", text_norm)) or text_norm.startswith("סעיף")
    return starts_like_agenda_item or "על סדר היום" in text_norm


def protocol_or_meeting_header_role(text_norm: str) -> str:
    action_exclusion_cues = REQUEST_ONLY_CUES + DECISION_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES + APPROVAL_DECISION_QUOTE_CUES
    if not text_has_any(text_norm, action_exclusion_cues):
        opening_cues = ("השתתפו", "נכחו בישיבה", "הישיבה נפתחה")
        officer_cues = ("יו\"ר", "היו\"ר", "היור", "מנכ\"ל", "מנכל", "מזכירת המועצה", "סטנוגרמה")
        if text_has_any(text_norm, opening_cues) and text_has_any(text_norm, officer_cues):
            return "meeting_header"
    if len(text_norm) > 900 and not text_has_any(text_norm, action_exclusion_cues):
        long_header_cues = (
            "השתתפו",
            "נכחו בישיבה",
            "הישיבה נפתחה",
            "מזכירת המועצה",
            "סטנוגרמה",
        )
        long_participant_cues = ("יו\"ר", "ראש העיר", "מנכ\"ל", "חברי המועצה", "חבר מועצה")
        if text_has_any(text_norm, long_header_cues) and text_has_any(text_norm, long_participant_cues):
            return "meeting_header"
    if len(text_norm) > 900:
        return ""
    early_meeting_metadata_hits = sum(1 for cue in MEETING_METADATA_CUES if normalize_for_search(cue) in text_norm)
    if early_meeting_metadata_hits >= 2:
        return "meeting_header"
    if text_has_any(text_norm, action_exclusion_cues):
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


def is_legal_meeting_basis_fragment(text_norm: str) -> bool:
    if len(text_norm) > 180:
        return False
    if text_has_any(text_norm, REQUEST_ONLY_CUES + DECISION_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES + APPROVAL_DECISION_QUOTE_CUES):
        return False
    return text_has_any(text_norm, ("סדר היום", "ישיבה מיוחדת")) and text_has_any(text_norm, ("סעיף", "פקודת העיריות", "לפקודת העיריות"))


def protocol_header_shape_for_linking(text_norm: str) -> bool:
    if len(text_norm) > 900:
        return False
    if normalize_for_search("פרוטוקול") not in text_norm:
        return False
    return text_has_any(text_norm, ("ועדה", "ועדת", "מס", "מתאריך", "החלטות"))


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
    root_label = compact_text(row.get("action_root_label_he") or row.get("subject_root_label_he"))[:180]
    child_label = compact_text(row.get("action_child_label_he") or row.get("subject_child_label_he"))[:180]
    if not root_label:
        failures.append("missing_subject_root_label")
        root_label = "נושא לא מזוהה"
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
            "subject_object_he": compact_text(row.get("subject_matter_he") or row.get("subject_object_he"))[:500],
            "subject_details_he": compact_text(row.get("action_details_he") or row.get("subject_details_he"))[:1000],
            "subject_type_evidence_he": compact_text(row.get("action_type_evidence_he") or row.get("subject_type_evidence_he"))[:500],
            "subject_summary_he": compact_text(row.get("action_summary_he") or row.get("subject_summary_he"))[:700],
            "action_root_label_he": root_label,
            "action_root_label_norm": normalize_for_search(root_label)[:500],
            "action_child_label_he": child_label,
            "action_child_label_norm": normalize_for_search(child_label)[:500],
            "subject_matter_he": compact_text(row.get("subject_matter_he") or row.get("subject_object_he"))[:500],
            "action_details_he": compact_text(row.get("action_details_he") or row.get("subject_details_he"))[:1000],
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

    if not bool(subject_payload.get("is_decision")):
        return

    decision = subject_payload.get("decision") if isinstance(subject_payload.get("decision"), dict) else {}
    repair_decision_quote_from_raw_text(artifact=artifact, subject_payload=subject_payload, decision=decision)
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
    procedural_decision_cues = AGENDA_REMOVAL_CUES + COMMITTEE_REFERRAL_CUES
    if text_has_any(quote_norm, REQUEST_ACTION_CUES) and not text_has_any(quote_norm, DECISION_ACTION_CUES + procedural_decision_cues):
        failures.append("request_or_proposal_without_decision_outcome")
    if text_has_any(quote_norm, RECOMMENDATION_ACTION_CUES) and not text_has_any(quote_norm, APPROVAL_VERB_CUES + STRONG_APPROVAL_ACTION_CUES):
        failures.append("recommendation_without_approval_outcome")
    if not text_has_any(quote_norm, DECISION_ACTION_CUES + procedural_decision_cues):
        failures.append("decision_action_not_grounded_in_source_quote")


def canonicalize_subject_taxonomy_fields(*, artifact: TopicDecisionArtifact, subject_payload: dict[str, Any]) -> None:
    root = compact_text(subject_payload.get("subject_root_label_he"))
    child = compact_text(subject_payload.get("subject_child_label_he"))
    object_text = compact_text(subject_payload.get("subject_object_he"))
    details = compact_text(subject_payload.get("subject_details_he"))
    evidence = compact_text(subject_payload.get("subject_type_evidence_he"))
    raw_text = corrected_hebrew_text(artifact.real_text)
    raw_norm = normalize_for_search(raw_text)

    original_root = remove_topic_from_label(label=root, artifact=artifact)
    original_child = remove_topic_from_label(label=child, artifact=artifact)
    if is_domain_noun_label(original_root) or is_domain_noun_label(original_child):
        object_text = merge_text_parts([object_text, original_root if is_domain_noun_label(original_root) else "", original_child if is_domain_noun_label(original_child) else ""])

    if is_condition_scope_text(raw_norm) and not text_has_any(raw_norm, REQUEST_ACTION_CUES) and not text_has_any(raw_norm, STRONG_APPROVAL_ACTION_CUES):
        root = "הגדרה"
        child = "הגדרת תנאים"
        object_text = object_text or condition_scope_object_from_text(raw_norm) or compact_text(artifact.topic_label_he)
        details = details or raw_text[:500]
        evidence = evidence or first_matching_cue(raw_text, CONDITION_SCOPE_CUES)
    else:
        root = normalize_root_subject_label(original_root, context_norm=raw_norm)
        child = normalize_child_subject_label(original_child, root=root, context_norm=raw_norm)
    root, child = normalize_municipal_action_taxonomy(root=root, child=child, context_norm=raw_norm)
    if root in {"הפניה לוועדה", "הסרה מסדר היום"}:
        inferred_object = procedural_subject_matter_from_text(raw_text)
        object_is_topic_copy = compact_text(object_text) in unique_strings([artifact.topic_label_he, artifact.root_label_he or "", artifact.child_label_he or ""])
        if inferred_object and (not object_text or object_is_topic_copy or not topic_label_supported_by_text(object_text, raw_text)):
            object_text = inferred_object
    if root in {"שאילתה", "מענה לשאילתה"}:
        inferred_subject = inquiry_subject_matter_from_context(artifact=artifact)
        object_is_topic_copy = compact_text(object_text) in unique_strings([artifact.topic_label_he, artifact.root_label_he or "", artifact.child_label_he or ""])
        if inferred_subject and (not object_text or object_is_topic_copy or not topic_label_supported_by_text(object_text, raw_text)):
            object_text = inferred_subject
    if root == "הצעה לסדר יום":
        inferred_subject = agenda_proposal_subject_matter_from_context(artifact=artifact)
        object_is_topic_copy = compact_text(object_text) in unique_strings([artifact.topic_label_he, artifact.root_label_he or "", artifact.child_label_he or ""])
        if inferred_subject and (not object_text or object_is_topic_copy or not topic_label_supported_by_text(object_text, raw_text)):
            object_text = inferred_subject
    if evidence and not subject_type_evidence_supported(quote=evidence, text=artifact.real_text):
        evidence = grounded_subject_type_evidence(root=root, raw_text=artifact.real_text) or evidence
    elif not evidence:
        evidence = grounded_subject_type_evidence(root=root, raw_text=artifact.real_text)

    if not object_text:
        if topic_label_supported_by_text(artifact.topic_label_he, raw_text):
            object_text = compact_text(artifact.topic_label_he)
        else:
            object_text = compact_text(subject_payload.get("what_text_is_about_he") or subject_payload.get("subject_summary_he"))[:500]
    if not details:
        details = compact_text(subject_payload.get("what_text_is_about_he") or subject_payload.get("subject_summary_he"))

    subject_payload["subject_root_label_he"] = root or "נושא"
    subject_payload["subject_root_label_norm"] = normalize_for_search(subject_payload["subject_root_label_he"])[:500]
    subject_payload["subject_child_label_he"] = child
    subject_payload["subject_child_label_norm"] = normalize_for_search(subject_payload["subject_child_label_he"])[:500]
    subject_payload["subject_object_he"] = object_text[:500]
    subject_payload["subject_details_he"] = details[:1000]
    subject_payload["subject_type_evidence_he"] = evidence[:500]
    subject_payload["action_root_label_he"] = subject_payload["subject_root_label_he"]
    subject_payload["action_root_label_norm"] = subject_payload["subject_root_label_norm"]
    subject_payload["action_child_label_he"] = subject_payload["subject_child_label_he"]
    subject_payload["action_child_label_norm"] = subject_payload["subject_child_label_norm"]
    subject_payload["subject_matter_he"] = subject_payload["subject_object_he"]
    subject_payload["action_details_he"] = subject_payload["subject_details_he"]


def remove_topic_from_label(*, label: str, artifact: TopicDecisionArtifact) -> str:
    value = compact_text(label)
    for topic_label in unique_strings([artifact.topic_label_he, artifact.root_label_he or "", artifact.child_label_he or ""]):
        topic = compact_text(topic_label)
        if not topic:
            continue
        value = value.replace(topic, "")
    value = re.sub(r"\s*[-–—:/]+\s*", " ", value)
    return compact_text(value)


def inquiry_subject_matter_from_context(*, artifact: TopicDecisionArtifact) -> str:
    texts = [corrected_hebrew_text(artifact.real_text)]
    for context in artifact.neighbor_contexts:
        if isinstance(context, dict):
            texts.append(corrected_hebrew_text(context.get("raw_text") or context.get("decision_context_text") or ""))
    for row in artifact.metadata.get("event_block_rows") or []:
        if isinstance(row, dict):
            texts.append(corrected_hebrew_text(row.get("raw_text") or row.get("corrected_text") or ""))
    combined = "\n".join(text for text in texts if text)
    patterns = [
        r"הנדון\s*[:：]?\s*שאילת[אה]\s*[–\-:]\s*(.{12,220})",
        r"שאילת[אה]\s+בנושא\s*(.{8,220})",
        r"שאילת[אה]\s*\d*(?:\.\d+)?\s+בנושא\s*(.{8,220})",
        r"שאילת[אה]\s*[–\-:]\s*(.{12,220})",
        r"אבקש\s+לדעת\s*(.{12,220})",
    ]
    for pattern in patterns:
        match = re.search(pattern, combined)
        if match:
            return cleanup_inquiry_subject_matter(match.group(1))
    question_match = re.search(r"(?:^|\s)\d+[.)]\s*(.{12,220}\?)", combined)
    if question_match:
        return cleanup_inquiry_subject_matter(question_match.group(1))
    return ""


def agenda_proposal_subject_matter_from_context(*, artifact: TopicDecisionArtifact) -> str:
    texts = [corrected_hebrew_text(artifact.real_text)]
    for context in artifact.neighbor_contexts:
        if isinstance(context, dict):
            texts.append(corrected_hebrew_text(context.get("raw_text") or context.get("decision_context_text") or ""))
    for row in artifact.metadata.get("event_block_rows") or []:
        if isinstance(row, dict):
            texts.append(corrected_hebrew_text(row.get("raw_text") or row.get("corrected_text") or ""))
    combined = "\n".join(text for text in texts if text)
    patterns = [
        r"הנדון\s*[:：]?\s*הצעה\s+לסדר\s+יום\s*[–\-:]\s*(.{8,220})",
        r"הצעה\s+לסדר\s+יום\s*[–\-:]\s*(.{8,220})",
        r"הצעה\s+לסדר\s*[–\-:]\s*(.{8,220})",
        r"הצעה\s+סדר\s+יום\s*[–\-:]\s*(.{8,220})",
    ]
    for pattern in patterns:
        match = re.search(pattern, combined)
        if match:
            return cleanup_procedural_subject_matter(match.group(1))
    return ""


def cleanup_inquiry_subject_matter(value: str) -> str:
    text = compact_text(value)
    text = re.split(r"[\s,;:]+(?:בקשתו|בקשתה|בקשתם|בקשה של|הוקראה|השאלה והתשובה|בסוף שנת|ראשית|במענה|להלן|בברכה|לכבוד)(?:\s|\d)", text, maxsplit=1)[0]
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,:;–-")[:220]


def cleanup_procedural_subject_matter(value: str) -> str:
    text = compact_text(value)
    text = re.split(r"\s(?:בקשתו|בקשתה|בקשתם|בקשה של|ראש העיר|חברי המועצה|ההצעה|הוקראה|השאלה והתשובה|לאור האמור|בעקבות|רקע)(?:\s|\d)", text, maxsplit=1)[0]
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,:;–-")[:220]


def normalize_root_subject_label(label: str, *, context_norm: str = "") -> str:
    value = compact_text(label)
    value_norm = normalize_for_search(value)
    combined = compact_text(f"{context_norm} {value_norm}")
    if has_response_to_inquiry_shape(context_norm + " " + value_norm):
        return "מענה לשאילתה"
    if text_has_any(context_norm + " " + value_norm, AGENDA_REMOVAL_CUES):
        return "הסרה מסדר היום"
    if text_has_any(context_norm + " " + value_norm, COMMITTEE_REFERRAL_CUES):
        return "הפניה לוועדה"
    if text_has_any(context_norm + " " + value_norm, AGENDA_PROPOSAL_CUES):
        return "הצעה לסדר יום"
    if is_condition_scope_text(context_norm) and not text_has_any(context_norm, REQUEST_ACTION_CUES) and not text_has_any(context_norm, STRONG_APPROVAL_ACTION_CUES):
        return "הגדרה"
    if text_has_any(context_norm + " " + value_norm, COMMITTEE_DECISION_APPROVAL_CUES):
        return "אישור החלטה"
    if has_inquiry_request_shape(context_norm + " " + value_norm) and not has_response_to_inquiry_shape(context_norm + " " + value_norm):
        return "שאילתה"
    if text_has_any(context_norm, REQUEST_ACTION_CUES) and not text_has_any(context_norm, DECISION_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES):
        return "בקשה"
    if looks_like_protocol_approval(combined):
        return "אישור פרוטוקול"
    if text_has_any(context_norm + " " + value_norm, RECOMMENDATION_ACTION_CUES) and not has_clear_approval_outcome(context_norm):
        return "המלצה"
    if value_norm.startswith(normalize_for_search("אישור")) or text_has_any(combined, APPROVAL_VERB_CUES + STRONG_APPROVAL_ACTION_CUES):
        return "אישור החלטה"
    if text_has_any(context_norm + " " + value_norm, RECOMMENDATION_ACTION_CUES):
        return "המלצה"
    if text_has_any(context_norm + " " + value_norm, REPORT_ACTION_CUES):
        return "דיווח"
    if text_has_any(context_norm + " " + value_norm, DIRECTIVE_ACTION_CUES):
        return "הנחיה"
    root_cues = [
        ("אישור", ("אישור", "אישר", "מאשר", "הוחלט לאשר")),
        ("בקשה", ("בקשה", "בקשת", "מבקש", "אודה לאישור", "אבקש")),
        ("קביעה", ("קביעה", "קביעת")),
        ("דיווח", ("דיון", "דיוני", "דיווח", "סקירה")),
        ("הצעה לסדר יום", AGENDA_PROPOSAL_CUES),
        ("המלצה", ("המלצה", "ממליצה", "ממליץ")),
        ("הנחיה", DIRECTIVE_ACTION_CUES),
        ("דיווח", NOTIFICATION_ACTION_CUES),
        ("הצבעה", VOTE_DETAIL_CUES),
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
    if root in {"שאילתה", "מענה לשאילתה"}:
        return ""
    if root == "הצעה לסדר יום":
        return ""
    if root in {"הסרה מסדר היום", "הפניה לוועדה"}:
        return ""
    if root == "אישור פרוטוקול":
        return ""
    if root == "אישור החלטה" and text_has_any(context_norm + " " + value_norm, ("ועדה", "וועדה", "הועדה", "הוועדה")):
        return "אישור החלטת ועדה"
    if root == "אישור החלטה":
        return ""
    if root in {"המלצה", "דיווח"}:
        if text_has_any(context_norm + " " + value_norm, ("שאילתה", "שאילתא", "במענה לשאילתה", "במענה לשאילתא")):
            return "מענה לשאילתה" if root == "דיווח" else ""
        if text_has_any(context_norm + " " + value_norm, ("סקירה", "מסכם", "מסכמת", "התקופה האחרונה")):
            return "סקירה"
        return ""
    if root == "הנחיה":
        if text_has_any(context_norm + " " + value_norm, COORDINATION_ACTION_CUES):
            return "הנחיה לתיאום מפגש"
        if text_has_any(context_norm + " " + value_norm, ("לעדכן", "יש לעדכן")):
            return "הנחיה לעדכון"
        return "הנחיה לפעול"
    if root == "בקשה" and text_has_any(context_norm + " " + value_norm, ("האצלת סמכויות", "האצלת סמכות")):
        return "בקשה להאצלת סמכויות"
    if root == "בקשה" and has_approval_request_shape(context_norm + " " + value_norm):
        return "בקשת אישור"
    if root == "בקשה" and has_inquiry_request_shape(context_norm + " " + value_norm):
        return "בקשת מידע"
    if root == "הגדרה" and (is_condition_scope_text(context_norm) or text_has_any(context_norm, DETAIL_CONDITION_CUES)):
        return "הגדרת תנאים"
    child_patterns = [
        ("אישור השתתפות", ("אישור השתתפות", "השתתפות")),
        ("בקשת אישור", ("בקשת אישור", "בקשה לאישור")),
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
    return value


def normalize_municipal_action_taxonomy(*, root: str, child: str, context_norm: str) -> tuple[str, str]:
    root = compact_text(root)
    child = compact_text(child)
    combined = compact_text(f"{context_norm} {root} {child}")
    if has_response_to_inquiry_shape(combined):
        return "מענה לשאילתה", ""
    if has_inquiry_request_shape(combined) and not has_response_to_inquiry_shape(combined):
        return "שאילתה", ""
    if text_has_any(combined, AGENDA_REMOVAL_CUES):
        return "הסרה מסדר היום", ""
    if text_has_any(combined, COMMITTEE_REFERRAL_CUES):
        return "הפניה לוועדה", ""
    if text_has_any(combined, AGENDA_PROPOSAL_CUES):
        return "הצעה לסדר יום", ""
    if root == "הגדרה" and is_condition_scope_text(context_norm) and not text_has_any(context_norm, REQUEST_ACTION_CUES) and not text_has_any(context_norm, STRONG_APPROVAL_ACTION_CUES):
        return "הגדרה", "הגדרת תנאים"
    if text_has_any(combined, COMMITTEE_DECISION_APPROVAL_CUES):
        return "אישור החלטה", "אישור החלטת ועדה"
    if text_has_any(combined, REQUEST_ACTION_CUES) and not text_has_any(combined, DECISION_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES):
        if has_inquiry_request_shape(combined):
            return "שאילתה", ""
        return "בקשה", "בקשת אישור" if has_approval_request_shape(combined) else child
    if text_has_any(combined, RECOMMENDATION_ACTION_CUES) and not has_clear_approval_outcome(context_norm):
        return "המלצה", ""
    if root in {"אישור", "אישור החלטה", "אישור פרוטוקול"} or text_has_any(combined, APPROVAL_VERB_CUES + STRONG_APPROVAL_ACTION_CUES):
        if looks_like_protocol_approval(combined):
            return "אישור פרוטוקול", ""
        return "אישור החלטה", normalize_child_subject_label(child, root="אישור החלטה", context_norm=context_norm)
    if text_has_any(combined, RECOMMENDATION_ACTION_CUES):
        return "המלצה", ""
    if text_has_any(combined, REPORT_ACTION_CUES):
        return "דיווח", normalize_child_subject_label(child, root="דיווח", context_norm=context_norm)
    if text_has_any(combined, DIRECTIVE_ACTION_CUES):
        return "הנחיה", normalize_child_subject_label(child, root="הנחיה", context_norm=context_norm)
    if root == "הודעה" or text_has_any(combined, NOTIFICATION_ACTION_CUES):
        return "דיווח", ""
    if root in {"תיאום", "ביצוע", "עדכון", "פעולה", "תרגיל"}:
        return root, child
    return root, child


def looks_like_protocol_approval(text_norm: str) -> bool:
    return text_has_any(text_norm, PROTOCOL_APPROVAL_CUES) or (
        text_has_any(text_norm, ("פרוטוקול", "הפרוטוקול"))
        and text_has_any(text_norm, APPROVAL_VERB_CUES + STRONG_APPROVAL_ACTION_CUES)
    )


def has_clear_approval_outcome(text_norm: str) -> bool:
    return text_has_any(text_norm, APPROVAL_DECISION_QUOTE_CUES)


def repair_decision_quote_from_raw_text(*, artifact: TopicDecisionArtifact, subject_payload: dict[str, Any], decision: dict[str, Any]) -> None:
    if not bool(subject_payload.get("is_decision")):
        return
    root = compact_text(subject_payload.get("subject_root_label_he"))
    if root not in {"אישור החלטה", "אישור פרוטוקול", "הסרה מסדר היום", "הפניה לוועדה"}:
        return
    source_quote = compact_text(decision.get("source_quote_he"))
    source_quote_norm = normalize_for_search(source_quote)
    quote_has_decision_action = text_has_any(source_quote_norm, DECISION_ACTION_CUES + AGENDA_REMOVAL_CUES + COMMITTEE_REFERRAL_CUES)
    if source_quote and quote_has_decision_action and quote_supported_by_text(quote=source_quote, text=artifact.real_text):
        return
    repaired = grounded_decision_source_quote(root=root, raw_text=artifact.real_text)
    if not repaired:
        return
    procedural_outcome_roots = {"הסרה מסדר היום", "הפניה לוועדה"}
    if root in procedural_outcome_roots or not compact_text(decision.get("decision_label_he")):
        decision["decision_label_he"] = root
        decision["decision_label_norm"] = normalize_for_search(root)[:500]
    decision["source_quote_he"] = repaired
    if root in procedural_outcome_roots or not compact_text(decision.get("decision_summary_he")):
        decision["decision_summary_he"] = compact_text(subject_payload.get("subject_details_he") or subject_payload.get("subject_summary_he") or subject_payload.get("what_text_is_about_he") or subject_payload.get("subject_object_he"))[:500]
    subject_payload["decision"] = decision


def grounded_decision_source_quote(*, root: str, raw_text: str) -> str:
    cues = APPROVAL_DECISION_QUOTE_CUES
    if root == "אישור פרוטוקול":
        cues = PROTOCOL_APPROVAL_CUES + APPROVAL_DECISION_QUOTE_CUES
    elif root == "הסרה מסדר היום":
        cues = AGENDA_REMOVAL_CUES + APPROVAL_DECISION_QUOTE_CUES
    elif root == "הפניה לוועדה":
        cues = COMMITTEE_REFERRAL_CUES + APPROVAL_DECISION_QUOTE_CUES
    return exact_source_quote_around_cue(raw_text=raw_text, cues=cues)


def exact_source_quote_around_cue(*, raw_text: str, cues: tuple[str, ...], max_words: int = 22) -> str:
    text = compact_text(raw_text)
    for cue in cues:
        cue_words = [re.escape(word) for word in compact_text(cue).split()]
        if not cue_words:
            continue
        pattern = r"\s+".join(cue_words)
        match = re.search(pattern, text)
        if not match:
            continue
        tail = text[match.start() :]
        return compact_text(" ".join(tail.split()[:max_words]))[:300]
    return ""


def procedural_subject_matter_from_text(raw_text: str) -> str:
    text = compact_text(corrected_hebrew_text(raw_text))
    text = re.sub(r"^סעיף\s*\d+\s*[:.]?\s*", "", text)
    text = re.sub(r"\bהצעה\s*\d*(?:\.\d+)?\b", "", text)
    text = re.sub(r"\s*-\s*לסדר\s*", " ", text)
    text = re.sub(r"\s+לסדר\s+", " ", text)
    text = compact_text(text)
    text = re.split(r"[\s,;:]+(?:בקשתו|בקשתה|בקשתם|בקשה של|ההצעה|ראש העיר|חברי המועצה|הוקראה|התקיים דיון)\b", text, maxsplit=1)[0]
    text = re.sub(r"\s*\([^)]{0,80}$", "", text)
    text = compact_text(text).strip(" .,:;–-")
    return text[:220]


def grounded_subject_type_evidence(*, root: str, raw_text: str) -> str:
    cues_by_root = {
        "שאילתה": ("שאילתה", "שאילתא", "אבקש לדעת"),
        "מענה לשאילתה": RESPONSE_TO_INQUIRY_ACTION_CUES,
        "בקשה": REQUEST_ACTION_CUES,
        "הצעה לסדר יום": AGENDA_PROPOSAL_CUES,
        "הסרה מסדר היום": AGENDA_REMOVAL_CUES,
        "הפניה לוועדה": COMMITTEE_REFERRAL_CUES,
        "אישור החלטה": APPROVAL_VERB_CUES + STRONG_APPROVAL_ACTION_CUES,
        "אישור פרוטוקול": PROTOCOL_APPROVAL_CUES + APPROVAL_VERB_CUES + STRONG_APPROVAL_ACTION_CUES,
        "המלצה": RECOMMENDATION_ACTION_CUES,
        "דיווח": REPORT_ACTION_CUES + NOTIFICATION_ACTION_CUES,
        "הנחיה": DIRECTIVE_ACTION_CUES,
    }
    return first_matching_cue(raw_text, cues_by_root.get(compact_text(root), ()))


def subject_type_evidence_supported(*, quote: str, text: str) -> bool:
    quote_norm = normalize_for_search(quote)
    return bool(quote_norm and quote_norm in normalize_for_search(text))


def is_condition_scope_text(text_norm: str) -> bool:
    return text_has_any(text_norm, CONDITION_SCOPE_CUES)


def has_approval_request_shape(text_norm: str) -> bool:
    return text_has_any(text_norm, ("אודה לאישור", "אודה לאשר", "מבקש לאשר", "מבקשת לאשר", "מבקשים לאשר", "בקשת אישור", "לאישור מועצת"))


def has_inquiry_request_shape(text_norm: str) -> bool:
    if has_response_to_inquiry_shape(text_norm):
        return False
    if has_approval_request_shape(text_norm):
        return False
    if text_has_any(text_norm, ("שאילתה", "שאילתא", "אבקש לדעת", "בקשת מידע")):
        return True
    return bool("?" in text_norm or {"מי", "מתי", "מה", "האם", "כיצד", "מדוע"} & set(text_norm.split()))


def has_response_to_inquiry_shape(text_norm: str) -> bool:
    return text_has_any(text_norm, RESPONSE_TO_INQUIRY_ACTION_CUES)


def is_domain_noun_label(label: str) -> bool:
    return text_has_any(label, DOMAIN_NOUN_ROOT_CUES)


def merge_text_parts(parts: list[str]) -> str:
    return "; ".join(unique_strings([compact_text(part) for part in parts if compact_text(part)]))


def subject_label_display(root: str, child: str) -> str:
    root = compact_text(root)
    child = compact_text(child)
    return f"{root} / {child}" if child else root


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
    subject_failure_prefixes = ("missing_subject_root_label", "empty_subject_root_norm")
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
                corrected_text=corrected_text_for_report(artifact=artifact, model_payload=model_payload),
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
        text_norm = normalize_for_search(corrected_hebrew_text(artifact.real_text))
        if is_condition_scope_text(text_norm) or not has_dialogue_action_evidence(text_norm):
            return TopicSubjectQualityRow(
                artifact_id=artifact.artifact_id,
                semantic_node_id=artifact.semantic_node_id,
                topic=artifact.topic_label_he,
                real_text=artifact.real_text,
                corrected_text=corrected_text_for_report(artifact=artifact, model_payload=model_payload),
                what_text_is_about=compact_text(model_payload.get("overall_summary_he")),
                artifact_role="background_context",
                topic_relevance="topic_bearing" if topic_label_supported_by_text(artifact.topic_label_he, artifact.real_text) else "topic_suspect",
                subject_root_by_dicta="",
                subject_child_by_dicta="",
                subject_object_by_dicta="",
                subject_details_by_dicta="",
                decision_by_dicta="",
                my_judgment="context_detail",
                ground_truth="Dicta לא החזירה נושא, והטקסט נראה כרקע/פרטים ללא פעולת דיאלוג עירונית עצמאית.",
                reason_for_failure="",
                status="context_detail",
                metadata={"model_payload": compact_subject_payload(model_payload)},
            )
        return TopicSubjectQualityRow(
            artifact_id=artifact.artifact_id,
            semantic_node_id=artifact.semantic_node_id,
            topic=artifact.topic_label_he,
            real_text=artifact.real_text,
            corrected_text=corrected_text_for_report(artifact=artifact, model_payload=model_payload),
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
    action_roots = " | ".join(unique_strings([str(item.subject_payload.get("action_root_label_he") or item.subject_payload.get("subject_root_label_he") or "") for item in extracted]))
    action_children = " | ".join(unique_strings([str(item.subject_payload.get("action_child_label_he") or item.subject_payload.get("subject_child_label_he") or "") for item in extracted]))
    subject_matters = " | ".join(unique_strings([str(item.subject_payload.get("subject_matter_he") or item.subject_payload.get("subject_object_he") or "") for item in extracted]))
    action_details = " | ".join(unique_strings([str(item.subject_payload.get("action_details_he") or item.subject_payload.get("subject_details_he") or "") for item in extracted]))
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
        corrected_text=corrected_text_for_report(artifact=artifact, model_payload=model_payload),
        what_text_is_about=about,
        artifact_role=artifact_roles,
        topic_relevance=topic_relevance,
        subject_root_by_dicta=roots,
        subject_child_by_dicta=children,
        subject_object_by_dicta=objects,
        subject_details_by_dicta=details,
        action_root_by_dicta=action_roots,
        action_child_by_dicta=action_children,
        subject_matter_by_dicta=subject_matters,
        action_details_by_dicta=action_details,
        decision_by_dicta=decision_by_dicta,
        my_judgment=my_judgment,
        ground_truth=ground_truth,
        reason_for_failure=reason,
        status=status,
        metadata={"model_payload": compact_subject_payload(model_payload)},
    )


def has_dialogue_action_evidence(text_norm: str) -> bool:
    return text_has_any(
        text_norm,
        REQUEST_ACTION_CUES
        + DECISION_ACTION_CUES
        + STRONG_APPROVAL_ACTION_CUES
        + RECOMMENDATION_ACTION_CUES
        + REPORT_ACTION_CUES
        + DIRECTIVE_ACTION_CUES
        + AGENDA_PROPOSAL_CUES,
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

            if is_non_municipal_taxonomy_subject(item=subject_item):
                mark_context_detail(artifact=artifact, item=subject_item, quality=quality)
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

        link_fragment_rows_to_nearby_validated_anchors(
            doc_artifacts=doc_artifacts,
            extracted_by_artifact=extracted_by_artifact,
            quality_by_artifact=quality_by_artifact,
            anchors=anchors,
        )


def link_fragment_rows_to_nearby_validated_anchors(
    *,
    doc_artifacts: list[TopicDecisionArtifact],
    extracted_by_artifact: dict[str, list[ExtractedTopicSubject]],
    quality_by_artifact: dict[str, TopicSubjectQualityRow],
    anchors: list[dict[str, Any]],
) -> None:
    for artifact in doc_artifacts:
        quality = quality_by_artifact.get(artifact.artifact_id)
        if quality is None:
            continue
        artifact_subjects = extracted_by_artifact.get(artifact.artifact_id, [])
        subject_item = next((item for item in artifact_subjects if item.validation_status != "failed"), None)
        if subject_item is None:
            continue
        if not is_fragment_row_link_candidate(artifact=artifact, item=subject_item, quality=quality):
            continue
        compatible_anchors = [
            anchor
            for anchor in anchors
            if is_current_validated_action_anchor(anchor) and anchor["artifact"].artifact_id != artifact.artifact_id
        ]
        anchor, score, reason = find_nearby_validated_anchor(artifact=artifact, item=subject_item, anchors=compatible_anchors)
        if anchor is not None and score >= fragment_link_threshold(artifact=artifact, item=subject_item, quality=quality):
            link_detail_to_anchor(
                artifact=artifact,
                item=subject_item,
                quality=quality,
                anchor=anchor,
                score=score,
                reason=f"nearby_validated_anchor; {reason}",
            )


def is_current_validated_action_anchor(anchor: dict[str, Any]) -> bool:
    quality = anchor.get("quality")
    return bool(
        not anchor.get("bridge")
        and str(anchor.get("anchor_status") or "") == "validated_anchor"
        and isinstance(quality, TopicSubjectQualityRow)
        and quality.row_role == "action_anchor"
    )


def is_fragment_row_link_candidate(*, artifact: TopicDecisionArtifact, item: ExtractedTopicSubject, quality: TopicSubjectQualityRow) -> bool:
    if quality.row_role in {"dependent_detail", "document_fragment", "context_detail", "vote_metadata", "no_subject"}:
        return False
    text_norm = normalize_for_search(corrected_hebrew_text(artifact.real_text))
    if protocol_or_meeting_header_role(text_norm) or protocol_header_shape_for_linking(text_norm):
        return False
    if has_weak_title_fragment_shape(text_norm):
        return True
    if quality.row_role not in {"suspect_anchor", "orphan_detail", "standalone_subject"}:
        return False
    payload = item.subject_payload
    root = compact_text(payload.get("subject_root_label_he"))
    child = compact_text(payload.get("subject_child_label_he"))
    evidence = compact_text(payload.get("subject_type_evidence_he"))
    grounded_evidence = evidence if evidence and subject_type_evidence_supported(quote=evidence, text=artifact.real_text) else ""
    return not has_anchor_action_evidence(
        root=root,
        child=child,
        text_norm=text_norm,
        evidence=grounded_evidence,
        is_decision=bool(payload.get("is_decision")),
    )


def has_weak_title_fragment_shape(text_norm: str) -> bool:
    if not text_norm or len(text_norm) > 260:
        return False
    if text_has_any(text_norm, DECISION_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES + REQUEST_ACTION_CUES + RECOMMENDATION_ACTION_CUES + DIRECTIVE_ACTION_CUES):
        return False
    starts_like_section = bool(re.match(r"^\s*(?:סעיף\s*)?\d+[).:\-\s]+", text_norm)) or text_norm.startswith("סעיף")
    has_title_cue = text_has_any(text_norm, ("הנדון", "נושא", "הודעה בדבר", "הודעה על", "החלטות", "סדר היום"))
    if starts_like_section and has_title_cue:
        return True
    return bool(has_title_cue and len(text_norm) <= 140)


def fragment_link_threshold(*, artifact: TopicDecisionArtifact, item: ExtractedTopicSubject, quality: TopicSubjectQualityRow) -> float:
    text_norm = normalize_for_search(corrected_hebrew_text(artifact.real_text))
    if has_weak_title_fragment_shape(text_norm):
        return 0.55
    return 0.62


def find_nearby_validated_anchor(*, artifact: TopicDecisionArtifact, item: ExtractedTopicSubject, anchors: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float, str]:
    best_anchor: dict[str, Any] | None = None
    best_score = 0.0
    best_reasons: list[str] = []
    skipped_reasons: list[str] = []
    ordered_anchors = sorted(anchors, key=lambda anchor: (abs(artifact.source_ordinal - anchor["artifact"].source_ordinal), anchor["artifact"].source_ordinal))
    for anchor in ordered_anchors[:6]:
        anchor_artifact: TopicDecisionArtifact = anchor["artifact"]
        distance = abs(artifact.source_ordinal - anchor_artifact.source_ordinal)
        if distance < 1 or distance > 3:
            continue
        shared = shared_context_token_count(
            merge_text_parts([anchor_artifact.real_text, anchor["item"].subject_payload.get("subject_object_he"), anchor["item"].subject_payload.get("subject_details_he")]),
            merge_text_parts([artifact.real_text, item.subject_payload.get("subject_object_he"), item.subject_payload.get("subject_details_he")]),
        )
        raw_shared = shared_context_token_count(anchor_artifact.real_text, artifact.real_text)
        compatible, compatibility_reason = anchor_context_compatible(artifact=artifact, anchor_artifact=anchor_artifact, distance=distance, shared=shared, raw_shared=raw_shared)
        if not compatible:
            skipped_reasons.append(compatibility_reason)
            continue
        score, reasons = link_score(artifact=artifact, item=item, anchor=anchor, distance=distance, shared=shared)
        if score > best_score:
            best_anchor = anchor
            best_score = score
            best_reasons = reasons
    reason = "; ".join(best_reasons) or "; ".join(skipped_reasons[-2:]) or "no_nearby_validated_anchor_with_sufficient_confidence"
    return best_anchor, round(best_score, 3), reason


def mark_non_subject_event_fields(*, quality: TopicSubjectQualityRow) -> None:
    if quality.status == "non_subject":
        quality.row_role = "document_fragment"
        quality.anchor_status = "not_anchor"
        quality.event_topic = ""
        return
    if quality.status == "context_detail":
        quality.row_role = "context_detail"
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
    return str(item.subject_payload.get("row_role") or "") not in {"dependent_detail", "document_fragment", "orphan_detail", "suspect_anchor", "context_detail", "vote_metadata"}


def is_action_anchor_subject(payload: dict[str, Any]) -> bool:
    root = compact_text(payload.get("subject_root_label_he"))
    return root in {"שאילתה", "מענה לשאילתה", "בקשה", "אישור", "אישור החלטה", "אישור פרוטוקול", "דחייה", "הפניה", "הצעה לסדר יום", "המלצה", "דיווח", "הנחיה", "מינוי", "הקצאה", "קביעה"}


def anchor_validation_status(*, artifact: TopicDecisionArtifact, item: ExtractedTopicSubject) -> tuple[str, str]:
    if not is_action_anchor_subject(item.subject_payload):
        return "not_anchor", "subject_root_is_not_action_anchor"
    text_norm = normalize_for_search(corrected_hebrew_text(artifact.real_text))
    if protocol_or_meeting_header_role(text_norm):
        return "suspect_anchor", "raw_text_looks_like_protocol_or_meeting_header"
    root = compact_text(item.subject_payload.get("subject_root_label_he"))
    child = compact_text(item.subject_payload.get("subject_child_label_he"))
    evidence = compact_text(item.subject_payload.get("subject_type_evidence_he"))
    if evidence and not subject_type_evidence_supported(quote=evidence, text=artifact.real_text):
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
    if root == "שאילתה":
        return has_inquiry_request_shape(combined)
    if root == "מענה לשאילתה":
        return has_response_to_inquiry_shape(combined)
    if root == "בקשה":
        return text_has_any(combined, REQUEST_ACTION_CUES)
    if root == "הצעה לסדר יום":
        return text_has_any(combined, AGENDA_PROPOSAL_CUES + ("מציע", "מבקש להעלות לדיון"))
    if root in {"אישור", "אישור החלטה", "אישור פרוטוקול"}:
        return text_has_any(combined, APPROVAL_VERB_CUES + STRONG_APPROVAL_ACTION_CUES)
    if root == "דחייה":
        return text_has_any(combined, ("נדחה", "דחייה", "דחתה", "לא אושר"))
    if root == "הפניה":
        return text_has_any(combined, ("הועבר", "להעביר", "הפניה", "הופנה"))
    if root == "דיון":
        return text_has_any(combined, ("דיון", "נדון", "דנו", "להעלות לדיון"))
    if root == "דיווח":
        return text_has_any(combined, REPORT_ACTION_CUES + NOTIFICATION_ACTION_CUES + ("נמסר", "עדכון"))
    if root == "המלצה":
        return text_has_any(combined, RECOMMENDATION_ACTION_CUES + ("ממליצה", "ממליץ", "המלצה"))
    if root == "הנחיה":
        return text_has_any(combined, DIRECTIVE_ACTION_CUES + COORDINATION_ACTION_CUES + EXECUTION_ACTION_CUES)
    if root == "מינוי":
        return text_has_any(combined, ("מינוי", "מונה", "למנות"))
    if root == "הקצאה":
        return text_has_any(combined, ("הקצאה", "להקצות", "הוקצה"))
    if root == "קביעה":
        return text_has_any(combined, ("קביעה", "קביעת", "נקבע", "קבעה"))
    return False


def is_non_municipal_taxonomy_subject(*, item: ExtractedTopicSubject) -> bool:
    payload = item.subject_payload
    root = compact_text(payload.get("subject_root_label_he"))
    child = compact_text(payload.get("subject_child_label_he"))
    if root in VOTE_METADATA_ROOTS:
        return True
    if root in NAKED_CONTEXT_ROOTS:
        return True
    if child in {"הגדרת הקשר", "הגדרת תנאים", "הגדרת סדר יום", "פעולה מיידית", "פעילות תשתית", "תרגול אירועי פח״ע", "תרגול אירועי פח\"ע", "נמנע מהצבעה", "הצבעת נמנע", "רישום משתתפים", "הסבר מקצועי"}:
        return True
    return False


def is_dependent_detail_candidate(*, artifact: TopicDecisionArtifact, item: ExtractedTopicSubject) -> bool:
    payload = item.subject_payload
    if bool(payload.get("is_decision")):
        return False
    text_norm = normalize_for_search(corrected_hebrew_text(artifact.real_text))
    if protocol_or_meeting_header_role(text_norm) or protocol_header_shape_for_linking(text_norm):
        return False
    if text_has_any(text_norm, REQUEST_ACTION_CUES) or text_has_any(text_norm, STRONG_APPROVAL_ACTION_CUES):
        return False
    root = compact_text(payload.get("subject_root_label_he"))
    child = compact_text(payload.get("subject_child_label_he"))
    detailish = root in {"הגדרה", "קביעה"} or child in {"הגדרת תנאים", "קביעת היקף"}
    if detailish and (is_condition_scope_text(text_norm) or has_detail_without_action_shape(artifact.real_text) or has_continuation_detail_shape(text_norm)):
        return True
    if is_action_anchor_subject(payload) and has_question_detail_shape(text_norm):
        return True
    if has_enumerated_proposal_detail_shape(text_norm):
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
    no_sentence_action = not text_has_any(compact, REQUEST_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES)
    return bool(punctuation_or_list and no_sentence_action)


def has_continuation_detail_shape(text_norm: str) -> bool:
    if text_has_any(text_norm, REQUEST_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES):
        return False
    if text_has_any(text_norm, DETAIL_CONDITION_CUES):
        return True
    if has_vote_detail_shape(text_norm):
        return True
    if re.match(r"^\s*\d+[).]?\s*(?:₪|שח|ש\s*ח|לסטודנטים|שעות)", text_norm):
        return True
    return False


def has_enumerated_proposal_detail_shape(text_norm: str) -> bool:
    if text_has_any(text_norm, REQUEST_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES + RECOMMENDATION_ACTION_CUES):
        return False
    if not re.match(r"^\s*\d+[).]?\s+", text_norm):
        return False
    return text_has_any(text_norm, EXECUTION_ACTION_CUES + DIRECTIVE_ACTION_CUES + ("פיצוי", "שיפוץ", "תשתיות", "טיפול"))


def has_question_detail_shape(text_norm: str) -> bool:
    if text_has_any(text_norm, REQUEST_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES):
        return False
    if "?" in text_norm:
        return True
    return bool({"מי", "מתי", "מה", "האם", "כיצד", "מדוע"} & set(text_norm.split()))


def has_vote_detail_shape(text_norm: str) -> bool:
    vote_hits = sum(1 for cue in VOTE_DETAIL_CUES if normalize_for_search(cue) in text_norm)
    return bool(vote_hits >= 1 and text_has_any(text_norm, ("שעות", "בברכה", "חברי", "ועדה")))


def find_compatible_anchor(*, artifact: TopicDecisionArtifact, item: ExtractedTopicSubject, anchors: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float, str]:
    best_anchor: dict[str, Any] | None = None
    best_score = 0.0
    best_reasons: list[str] = []
    skipped_reasons: list[str] = []
    for anchor in reversed(anchors[-3:]):
        if str(anchor.get("anchor_status") or "") != "validated_anchor":
            continue
        anchor_artifact: TopicDecisionArtifact = anchor["artifact"]
        distance = abs(artifact.source_ordinal - anchor_artifact.source_ordinal)
        if distance < 1 or distance > 3:
            continue
        shared = shared_context_token_count(
            merge_text_parts([anchor_artifact.real_text, anchor["item"].subject_payload.get("subject_object_he"), anchor["item"].subject_payload.get("subject_details_he")]),
            merge_text_parts([artifact.real_text, item.subject_payload.get("subject_object_he"), item.subject_payload.get("subject_details_he")]),
        )
        raw_shared = shared_context_token_count(anchor_artifact.real_text, artifact.real_text)
        compatible, compatibility_reason = anchor_context_compatible(artifact=artifact, anchor_artifact=anchor_artifact, distance=distance, shared=shared, raw_shared=raw_shared)
        if not compatible:
            skipped_reasons.append(compatibility_reason)
            continue
        score, reasons = link_score(artifact=artifact, item=item, anchor=anchor, distance=distance, shared=shared)
        if score > best_score:
            best_anchor = anchor
            best_score = score
            best_reasons = reasons
    reason = "; ".join(best_reasons) or "; ".join(skipped_reasons[-2:]) or "no_validated_anchor_with_sufficient_confidence"
    return best_anchor, round(best_score, 3), reason


def anchor_context_compatible(*, artifact: TopicDecisionArtifact, anchor_artifact: TopicDecisionArtifact, distance: int, shared: int, raw_shared: int) -> tuple[bool, str]:
    same_topic = compact_text(anchor_artifact.topic_label_he) == compact_text(artifact.topic_label_he)
    if same_topic:
        return True, "same_artifact_topic"
    if shared >= 2 and raw_shared >= 1:
        return True, "shared_concrete_context_terms"

    text_norm = normalize_for_search(artifact.real_text)
    if distance == 1 and is_condition_scope_text(text_norm):
        return True, "adjacent_scope_or_condition_continuation"
    if distance == 1 and has_vote_detail_shape(text_norm) and raw_shared >= 1:
        return True, "adjacent_vote_continuation_with_shared_context"
    if distance == 1 and has_question_detail_shape(text_norm) and raw_shared >= 1:
        return True, "adjacent_question_continuation_with_shared_context"
    return False, "different_topic_without_shared_context"


def link_score(*, artifact: TopicDecisionArtifact, item: ExtractedTopicSubject, anchor: dict[str, Any], distance: int, shared: int) -> tuple[float, list[str]]:
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
    text_norm = normalize_for_search(corrected_hebrew_text(artifact.real_text))
    if is_condition_scope_text(text_norm):
        score += 0.15
        reasons.append("current_row_is_scope_or_conditions")
    if has_enumerated_proposal_detail_shape(text_norm):
        score += 0.18
        reasons.append("current_row_is_numbered_proposal_detail")
    if has_vote_detail_shape(text_norm) and not bool(anchor.get("bridge")) and is_action_anchor_subject(anchor_item.subject_payload):
        score += 0.22
        reasons.append("current_row_is_vote_tally_for_previous_action")
    if shared >= 2:
        score += 0.15
        reasons.append("shared_object_or_detail_terms")
    elif shared == 1:
        score += 0.08
        reasons.append("one_shared_term")
    if compact_text(anchor_artifact.source_title) == compact_text(artifact.source_title):
        score += 0.08
        reasons.append("same_source_document_title")
    if text_has_any(text_norm, REQUEST_ACTION_CUES + STRONG_APPROVAL_ACTION_CUES):
        score -= 0.4
        reasons.append("current_row_has_own_action")
    return max(0.0, min(1.0, score)), reasons


def shared_context_token_count(left: str, right: str) -> int:
    stop = {
        "של",
        "את",
        "על",
        "עם",
        "או",
        "כל",
        "לפי",
        "דין",
        "כדין",
        "מועצת",
        "העיר",
        "בקשה",
        "בקשת",
        "אישור",
        "הצעה",
        "החלטות",
        "פרוטוקול",
        "סעיף",
        "חברי",
        "המועצה",
        "בקשתו",
        "בקשתה",
        "בקשתם",
        "לסדר",
        "לדיון",
        "דיון",
    }
    left_tokens = {token for token in normalize_for_search(left).split() if len(token) >= 4 and token not in stop and not re.search(r"\d", token)}
    right_tokens = {token for token in normalize_for_search(right).split() if len(token) >= 4 and token not in stop and not re.search(r"\d", token)}
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
    quality.action_root_by_dicta = compact_text(anchor_payload.get("action_root_label_he") or anchor_payload.get("subject_root_label_he"))
    quality.action_child_by_dicta = compact_text(anchor_payload.get("action_child_label_he") or anchor_payload.get("subject_child_label_he"))
    quality.subject_matter_by_dicta = compact_text(anchor_payload.get("subject_matter_he") or anchor_payload.get("subject_object_he")) or quality.subject_matter_by_dicta
    quality.action_details_by_dicta = compact_text(anchor_payload.get("action_details_he") or anchor_payload.get("subject_details_he")) or quality.action_details_by_dicta
    quality.my_judgment = "linked_dependent_detail"
    quality.ground_truth = f"הקטע אינו נושא עצמאי; הוא מוסיף פרטים לאירוע סמוך מסוג {subject_label_display(quality.subject_root_by_dicta, quality.subject_child_by_dicta)}."
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


def mark_context_detail(*, artifact: TopicDecisionArtifact, item: ExtractedTopicSubject, quality: TopicSubjectQualityRow) -> None:
    root = compact_text(item.subject_payload.get("subject_root_label_he"))
    row_role = "vote_metadata" if root in VOTE_METADATA_ROOTS else "context_detail"
    item.validation_status = row_role
    item.subject_payload["row_role"] = row_role
    item.subject_payload["anchor_status"] = "not_anchor"
    item.subject_payload["link_confidence"] = 0.0
    item.subject_payload["link_reason"] = "not_municipal_action_taxonomy"
    quality.event_id = ""
    quality.event_topic = ""
    quality.row_role = row_role
    quality.anchor_status = "not_anchor"
    quality.linked_event_id = ""
    quality.link_confidence = 0.0
    quality.link_reason = "not_municipal_action_taxonomy"
    quality.topic_relevance = "topic_bearing"
    quality.artifact_role = "vote_metadata" if row_role == "vote_metadata" else "background_context"
    quality.my_judgment = row_role
    quality.ground_truth = "הקטע גלוי בדוח לצורך ביקורת, אך אינו נושא פעולה עירונית עצמאי ולכן אינו נכנס לעץ הנושאים."
    quality.reason_for_failure = "not_municipal_action_taxonomy"
    quality.status = row_role


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
    if not event_topic and quality.topic_relevance == "topic_bearing":
        quality.topic_relevance = "topic_suspect"
        item.subject_payload["topic_relevance"] = "topic_suspect"
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
    text = corrected_hebrew_text(artifact.real_text)
    if topic_label_supported_by_text(artifact.topic_label_he, text) or topic_label_supported_by_text(artifact.topic_label_he, object_text):
        return artifact.topic_label_he
    if artifact.child_label_he and topic_label_supported_by_text(artifact.child_label_he, text + " " + object_text):
        return artifact.child_label_he
    return ""


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
            action_root_label_he=string_or_none(subject.get("action_root_label_he") or subject.get("subject_root_label_he")),
            action_root_label_norm=string_or_none(subject.get("action_root_label_norm") or subject.get("subject_root_label_norm")),
            action_child_label_he=string_or_none(subject.get("action_child_label_he") or subject.get("subject_child_label_he")),
            action_child_label_norm=string_or_none(subject.get("action_child_label_norm") or subject.get("subject_child_label_norm")),
            subject_matter_he=string_or_none(subject.get("subject_matter_he") or subject.get("subject_object_he")),
            action_details_he=string_or_none(subject.get("action_details_he") or subject.get("subject_details_he")),
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
            action_root_by_dicta=quality_row.action_root_by_dicta or quality_row.subject_root_by_dicta,
            action_child_by_dicta=quality_row.action_child_by_dicta or quality_row.subject_child_by_dicta,
            subject_matter_by_dicta=quality_row.subject_matter_by_dicta or quality_row.subject_object_by_dicta,
            action_details_by_dicta=quality_row.action_details_by_dicta or quality_row.subject_details_by_dicta,
            decision_by_dicta=quality_row.decision_by_dicta,
            my_judgment=quality_row.my_judgment,
            ground_truth=quality_row.ground_truth,
            reason_for_failure=quality_row.reason_for_failure,
            status=quality_row.status,
            metadata_json=json.dumps({**quality_row.metadata, "corrected_text_he": quality_row.corrected_text}, ensure_ascii=False),
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
    event_blocks_json_path = output_dir / "event_blocks.json"
    event_blocks_md_path = output_dir / "event_blocks.md"
    tree_path.write_text(topic_tree_markdown(result.topic_tree), encoding="utf-8")
    artifacts_path.write_text(artifacts_markdown(result.artifacts), encoding="utf-8")
    subject_tree_path.write_text(subject_tree_markdown(result.extracted_subjects), encoding="utf-8")
    quality_path.write_text(quality_report_markdown(result.quality_rows), encoding="utf-8")
    quality_json_path.write_text(json.dumps([quality_row_to_dict(row) for row in result.quality_rows], ensure_ascii=False, indent=2), encoding="utf-8")
    event_blocks_json_path.write_text(json.dumps(result.event_blocks, ensure_ascii=False, indent=2), encoding="utf-8")
    event_blocks_md_path.write_text(event_blocks_markdown(result.event_blocks), encoding="utf-8")
    return {
        "topic_tree_md": str(tree_path),
        "artifacts_md": str(artifacts_path),
        "subject_tree_md": str(subject_tree_path),
        "quality_report_md": str(quality_path),
        "quality_report_json": str(quality_json_path),
        "event_blocks_json": str(event_blocks_json_path),
        "event_blocks_md": str(event_blocks_md_path),
    }


def subject_tree_markdown(subjects: list[ExtractedTopicSubject]) -> str:
    lines = ["## Action Tree Candidates", "", "| Action Root Candidate | Action Child Candidate | Support | Decision Candidates | Example Source Topic | Review Status |", "|---|---|---:|---:|---|---|"]
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for item in subjects:
        if not is_countable_subject_event(item):
            continue
        subject = item.subject_payload
        root = str(subject.get("subject_root_label_he") or "")
        child = str(subject.get("subject_child_label_he") or "")
        if not root:
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
        "## Topic Action/Subject-Matter Quality Report",
        "",
        "| Source Topic | Event Topic | Topic Relevance | Row Role | Anchor Status | Artifact Role | Link Confidence | Corrected Text | Raw Text | What Text Is About | Action Root By Dicta | Action Child By Dicta | Subject Matter | Action Details | Decision By Dicta | My Judgment | Ground Truth | Reason For Failure |",
        "|---|---|---|---|---|---|---:|---|---|---|---|---|---|---|---|---|---|---|",
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
                    escape_table(row.corrected_text or row.real_text),
                    escape_table(row.real_text),
                    escape_table(shorten(row.what_text_is_about, 220)),
                    escape_table(shorten(row.action_root_by_dicta or row.subject_root_by_dicta, 160)),
                    escape_table(shorten(row.action_child_by_dicta if row.action_child_by_dicta else row.subject_child_by_dicta, 160)),
                    escape_table(shorten(row.subject_matter_by_dicta or row.subject_object_by_dicta, 160)),
                    escape_table(shorten(row.action_details_by_dicta or row.subject_details_by_dicta, 180)),
                    escape_table(shorten(row.decision_by_dicta, 180)),
                    escape_table(row.my_judgment),
                    escape_table(shorten(row.ground_truth, 220)),
                    escape_table(shorten(row.reason_for_failure, 220)),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def event_blocks_markdown(blocks: list[dict[str, Any]]) -> str:
    lines = [
        "## Topic Subject Event Blocks",
        "",
        "| Block ID | Kind | Anchor Artifact | Rows |",
        "|---|---|---|---|",
    ]
    for block in blocks:
        rows = []
        for row in block.get("rows") or []:
            if not isinstance(row, dict):
                continue
            rows.append(f"{row.get('role') or ''}:{row.get('artifact_id') or ''}:{shorten(str(row.get('raw_text') or ''), 90)}")
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_table(str(block.get("block_id") or "")),
                    escape_table(str(block.get("kind") or "")),
                    escape_table(str(block.get("anchor_artifact_id") or "")),
                    escape_table(" ; ".join(rows)),
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
        "corrected_text": row.corrected_text,
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
        "action_root_by_dicta": row.action_root_by_dicta or row.subject_root_by_dicta,
        "action_child_by_dicta": row.action_child_by_dicta if row.action_child_by_dicta else row.subject_child_by_dicta,
        "subject_matter_by_dicta": row.subject_matter_by_dicta or row.subject_object_by_dicta,
        "action_details_by_dicta": row.action_details_by_dicta or row.subject_details_by_dicta,
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
    return {key: payload.get(key) for key in ("error_code", "error_text", "corrected_text_he", "overall_summary_he", "rationale_he", "subjects", "preclassified", "non_subject", "artifact_role", "topic_relevance", "json_repair_applied") if key in payload}
