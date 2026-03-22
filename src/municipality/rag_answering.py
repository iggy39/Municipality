from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from municipality.chunking import normalize_for_search
from municipality.fallback import BYTEZ_PROVIDER
from municipality.rag_llm import RAG_CALL_ANSWER, RAG_CALL_REFUSE, RAG_CALL_VERIFY, RagLlmClient
from municipality.rag_observability import log_rag_event
from municipality.rag_retrieval import RagContextChunk, RagRetrievalResult


REASON_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
REASON_MISSING_MIXED_SOURCE_EVIDENCE = "MISSING_MIXED_SOURCE_EVIDENCE"
REASON_MISSING_PROTOCOL_EVIDENCE = "MISSING_PROTOCOL_EVIDENCE"
REASON_MISSING_ATTACHMENT_EVIDENCE = "MISSING_ATTACHMENT_EVIDENCE"
REASON_ANSWER_GENERATION_FAILED = "ANSWER_GENERATION_FAILED"
REASON_INVALID_ANSWER_FORMAT = "INVALID_ANSWER_FORMAT"
REASON_UNCITED_CLAIMS = "UNCITED_CLAIMS"
REASON_VERIFICATION_FAILED = "VERIFICATION_FAILED"
REASON_INVALID_VERIFICATION_FORMAT = "INVALID_VERIFICATION_FORMAT"
REASON_UNSUPPORTED_CLAIMS = "UNSUPPORTED_CLAIMS"
REASON_TOPIC_MISMATCH_EVIDENCE = "TOPIC_MISMATCH_EVIDENCE"
REASON_NO_DECISION_CONTENT = "NO_DECISION_CONTENT"

LOCAL_VERIFY_PROVIDER_NAME = "TinyLlamaLocal"
LOCAL_VERIFY_MODEL_DIR_DEFAULT = "local_llm/models/TinyLlama-1.1B-Chat-v1.0"
VERIFY_FALLBACK_PROVIDER_EXTERNAL = "external"
VERIFY_FALLBACK_PROVIDER_LOCAL = "local"
DETERMINISTIC_SIMILARITY_SOURCE = "deterministic_similarity"

CLAIM_SCORE_HIGH_MIN = 0.67
CLAIM_SCORE_MEDIUM_MIN = 0.34
LOW_SCORE_WARNING_LIMIT = 0.34

CLAIM_TOPIC_COVERAGE_WEIGHT = 0.75
EVIDENCE_TOPIC_COVERAGE_WEIGHT = 0.15
SUPPORT_HINT_BONUS = 0.15
CROSS_DOMAIN_PENALTY = 0.2

STRONG_DECISION_CLAIM_MARKERS = {
    "הוחלט",
    "הוחלטה",
    "הוחלטו",
    "החלטה",
    "מאשר",
    "מאשרים",
    "מאושרת",
    "מאושר",
    "אושר",
    "אושרה",
    "אושרו",
    "אישר",
    "אישרה",
    "אישרו",
    "נדחה",
    "נדחתה",
    "נדחו",
    "בוטל",
    "בוטלה",
    "בוטלו",
}

DISCUSSION_CLAIM_MARKERS = {
    "נדון",
    "נדונה",
    "נדונו",
}

PLACEHOLDER_SUMMARY_RE = re.compile(r"^טענה(?:\s+מבוססת)?(?:\s+\d+)?$")


def rag_answering_thresholds_snapshot() -> dict[str, Any]:
    verify_routing = RagVerifyRoutingConfig.from_env()
    return {
        "evidence_gate": {
            "missing_source_rule": "refuse if any required source kind is missing",
            "topic_mismatch_rule": "reject if mismatch_count > 0",
        },
        "claim_score_thresholds": {
            "high_min": CLAIM_SCORE_HIGH_MIN,
            "medium_min": CLAIM_SCORE_MEDIUM_MIN,
            "low_warning_below": LOW_SCORE_WARNING_LIMIT,
        },
        "answer_claim_selection": {
            "decision_markers_strong": sorted(STRONG_DECISION_CLAIM_MARKERS),
            "discussion_markers_non_decision": sorted(DISCUSSION_CLAIM_MARKERS),
            "decision_gate_basis": "strong_markers_only",
            "drop_non_decision_when_decision_claims_present": True,
            "decision_required_rule": "require when query has <=1 primary topic token",
            "max_reconstructed_decision_lines": 4,
        },
        "fallback_heuristic_weights": {
            "claim_topic_coverage_weight": CLAIM_TOPIC_COVERAGE_WEIGHT,
            "evidence_topic_coverage_weight": EVIDENCE_TOPIC_COVERAGE_WEIGHT,
            "support_hint_bonus": SUPPORT_HINT_BONUS,
            "cross_domain_penalty": CROSS_DOMAIN_PENALTY,
        },
        "semantic_similarity_rubric": {
            "core_direct": "0.90-1.00",
            "related_operational": "0.65-0.85",
            "adjacent_context": "0.35-0.64",
            "unrelated": "<0.35",
        },
        "verify_routing": verify_routing.as_dict(),
    }


@dataclass(slots=True)
class RagVerifyRoutingConfig:
    fallback_provider: str = VERIFY_FALLBACK_PROVIDER_EXTERNAL
    deterministic_low_score_threshold: float = LOW_SCORE_WARNING_LIMIT
    local_model_dir: str = LOCAL_VERIFY_MODEL_DIR_DEFAULT
    local_device: str = "cpu"
    local_max_new_tokens: int = 220
    local_timeout_seconds: float = 90.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RagVerifyRoutingConfig:
        source = env if env is not None else os.environ

        fallback_provider = _normalize_fallback_provider(source.get("RAG_VERIFY_FALLBACK_PROVIDER"))
        deterministic_low_score_threshold = _env_float(
            source.get("RAG_VERIFY_DETERMINISTIC_LOW_THRESHOLD"),
            default=LOW_SCORE_WARNING_LIMIT,
            min_value=0.0,
            max_value=1.0,
        )

        local_model_dir = (source.get("RAG_VERIFY_LOCAL_MODEL_DIR") or LOCAL_VERIFY_MODEL_DIR_DEFAULT).strip()
        if not local_model_dir:
            local_model_dir = LOCAL_VERIFY_MODEL_DIR_DEFAULT

        local_device = (source.get("RAG_VERIFY_LOCAL_DEVICE") or "cpu").strip().casefold()
        if local_device not in {"auto", "cpu", "cuda"}:
            local_device = "cpu"

        local_max_new_tokens = _env_int(
            source.get("RAG_VERIFY_LOCAL_MAX_NEW_TOKENS"),
            default=220,
            min_value=64,
            max_value=512,
        )
        local_timeout_seconds = _env_float(
            source.get("RAG_VERIFY_LOCAL_TIMEOUT_SECONDS"),
            default=90.0,
            min_value=20.0,
            max_value=300.0,
        )

        return cls(
            fallback_provider=fallback_provider,
            deterministic_low_score_threshold=deterministic_low_score_threshold,
            local_model_dir=local_model_dir,
            local_device=local_device,
            local_max_new_tokens=local_max_new_tokens,
            local_timeout_seconds=local_timeout_seconds,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "fallback_provider": self.fallback_provider,
            "deterministic_low_score_threshold": self.deterministic_low_score_threshold,
            "local_model_dir": self.local_model_dir,
            "local_device": self.local_device,
            "local_max_new_tokens": self.local_max_new_tokens,
            "local_timeout_seconds": self.local_timeout_seconds,
        }

TOPIC_SUPPORT_HINT_TOKENS = {
    "בטיחות",
    "בדרכים",
    "תנועה",
    "תחבורה",
    "כביש",
    "כבישים",
    "תמרור",
    "תמרורים",
    "אוטובוס",
    "חציה",
    "צומת",
    "נהג",
    "נהגים",
    "רכב",
    "רכבים",
    "סיכון",
    "הולכי",
    "רגל",
}

SECONDARY_CONTEXT_HINT_TOKENS = {
    "בית",
    "הספר",
    "חינוך",
    "גן",
    "גנים",
    "ילדים",
    "מעון",
    "מעונות",
    "רווחה",
}


TOPIC_CLAUSE_SPLIT_RE = re.compile(r"\b(?:וגם|ומה|בנוסף)\b")
HEBREW_TOKEN_RE = re.compile(r"[\u0590-\u05FF]{2,}")
NUMBERED_DECISION_RE = re.compile(r"(?:^|\n)\.(\d+)\s*(.*?)(?=(?:\n\.\d+)|\Z)", re.S)
DECISION_CLAUSE_SPLIT_RE = re.compile(
    r",\s*(?=(?:כמו\s+כן\s+)?(?:מאשרים|מאשר|מאושרת|מאושר|אושרה|אושרו|אושר|הוחלט|הוחלטה|הוחלטו|נדחה|נדחתה|נדחו|בוטל|בוטלה|בוטלו))"
)
DECISION_CONNECTOR_SPLIT_RE = re.compile(r"\bכמו\s+כן\b")
DECISION_FALLBACK_PHRASE_RE = re.compile(r"(?:הזמנה\s+תקציבית|הוצאת\s+הזמנה|הצעת\s+מחיר|לאשר|אישור)")
PROTOCOL_HEADLINE_RE = re.compile(r"(?:^|\n)\s*([^:\n]{6,120})\s*:")
HEBREW_HEADLINE_TOKEN_RE = re.compile(r"[\u0590-\u05FF](?:[\u0590-\u05FF'\"׳״]*)")
GENERIC_QUERY_TOKENS = {
    "אילו",
    "איזה",
    "איזו",
    "מה",
    "מי",
    "למה",
    "כמה",
    "מתי",
    "האם",
    "הוחלט",
    "החלטה",
    "החלטות",
    "התקבל",
    "התקבלו",
    "אושר",
    "אושרה",
    "אישר",
    "אישרה",
    "מועצת",
    "העיר",
    "עיר",
    "בעיר",
    "עירייה",
    "העירייה",
    "בפרוטוקול",
    "פרוטוקול",
}

PROTOCOL_TITLE_STOP_TOKENS = {
    "פרוטוקול",
    "ועדה",
    "ועדת",
    "הועדה",
    "הוועדה",
    "מספר",
    "מספור",
    "חתימות",
    "סרוק",
    "מונגש",
}

TOPIC_NOISE_TOKENS = {
    "הוחלט",
    "החלטה",
    "החלטות",
    "מאשר",
    "מאשרים",
    "אושר",
    "אושרה",
    "אושרו",
    "לקדם",
    "להוסיף",
    "להעלות",
    "לבצע",
    "להעמיק",
    "להכליל",
    "לאשר",
    "אישור",
    "מבוקש",
    "עבור",
    "בנושא",
    "נושא",
    "במקומות",
    "המתאימים",
    "המשך",
    "בשנית",
}

TOPIC_RELATIONAL_EDGE_TOKENS = {
    "לגבי",
    "בנוגע",
    "לצורך",
    "עבור",
    "באשר",
    "בעקבות",
    "בתחום",
    "בתחומי",
    "בנושא",
    "בנושאי",
    "לנושא",
    "מול",
    "בין",
    "תוך",
    "אצל",
    "ללא",
    "לשם",
    "כדי",
    "אודות",
}

TOPIC_TOKEN_NORMALIZATION = {
    "בבקשות": "בקשות",
    "למאבק": "מאבק",
    "במאבק": "מאבק",
    "בנגע": "נגע",
    "להקצאת": "להקצאה",
    "בהקצאת": "הקצאה",
    "לועדת": "ועדה",
    "בועדת": "ועדה",
    "בוועדה": "ועדה",
    "הפעולה": "פעולה",
    "הקשר": "קשר",
    "הנושא": "נושא",
    "הנושאים": "נושאים",
}

TOPIC_GENERIC_FRAGMENT_TOKENS = {
    "שיתוף",
    "פעולה",
    "קשר",
    "נושא",
    "נושאים",
    "עניין",
    "תהליך",
    "מערך",
    "מהלך",
    "תחום",
}

TOPIC_ACTION_HEAD_TOKENS = {
    "הוספת",
    "בדיקת",
    "ביצוע",
    "קידום",
    "העמקת",
    "הכללת",
    "הזזת",
    "הפעלת",
    "הרחבת",
    "עדכון",
    "אישור",
}

TOPIC_DETAIL_PREPOSITION_PREFIXES = {"ב", "ל", "מ", "כ", "ו"}
TOPIC_MAX_TOKENS = 4
TOPIC_INSTRUMENT_ONLY_TOKENS = {
    "הזמנה",
    "תקציבית",
    "הזמנות",
    "תקציביות",
    "הצעת",
    "מחיר",
    "אישור",
    "אומדן",
    "אומדנים",
}
SEMANTIC_TOPIC_STOP_TOKENS = {
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
SEMANTIC_TOPIC_GENERIC_TOKENS = {
    "בקשה",
    "בקשות",
    "הקצאה",
    "להקצאה",
    "להקצאת",
    "דיון",
    "בבקשות",
    "בבקשה",
    "כללי",
}
SUBJECTLESS_PUBLICATION_PATTERNS = [
    re.compile(r"פרסום\s+(?:זמני|ראשון|שני)(?:\s+ו(?:זמני|ראשון|שני))?\s+בעיתונות"),
    re.compile(r"פרסום\s+בעיתונות"),
    re.compile(r"פרסום\s+[^\n\.]{1,50}\s+בעיתונות"),
]
OBJECT_ROOT_KEYWORDS: dict[str, set[str]] = {
    "הסכמים": {"הסכם", "הסכמים", "חוזה", "חוזים", "רשות"},
    "הקצאות": {"הקצאה", "הקצאות", "להקצאה", "להקצאת", "בקשה", "בקשות", "עמותה", "עמותה"},
    "תמרורים": {"תמרור", "תמרורים"},
    "בטיחות בדרכים": {"בטיחות", "דרכים"},
    "ניקיון": {"ניקיון"},
    "אבטחה": {"אבטחה", "אבטחת"},
}

SUMMARY_BOILERPLATE_PATTERNS = [
    re.compile(r"פרסום\s+(?:זמני|ראשון|שני)(?:\s+ו(?:זמני|ראשון|שני))?\s+בעיתונות"),
    re.compile(r"בית\s+העירייה"),
    re.compile(r"רח\s*['\"]"),
    re.compile(r"ת\s*\.?\s*ד\s*\.?"),
]
LOW_QUALITY_SUMMARY_PATTERNS = [
    re.compile(r"^\d+\s*/\s*\d+"),
    re.compile(r"בפרוטוקול\s+מס"),
    re.compile(r"מס\s*['\"׳״]?\s*[:\-]?\s*\d+"),
]
PROCEDURAL_ALLOCATION_SUMMARY_PATTERNS = [
    re.compile(r"מאשרים\s+החלטת\s+הועדה\s+המקצועית\s+להקצאות\s+קרקע"),
    re.compile(r"פרסום\s+שני\s+בעיתונות"),
    re.compile(r"פרסום\s+החלטות\s+הוועדה\s+בעיתונות"),
]
PARCEL_GUSH_RE = re.compile(r"גוש\s*[:\-]?\s*(\d{2,6})")
PARCEL_GUSH_COMPACT_RE = re.compile(r"גוש(\d{2,6})")
PARCEL_HELKA_RE = re.compile(r"חלקה\s*[:\-]?\s*(\d{1,6})")
PARCEL_MIGRASH_RE = re.compile(r"מגרש\s*[:\-]?\s*(\d{1,6})")
REQUEST_SUBJECT_RE = re.compile(r"מהות\s+הבקשה\s*:\s*(.+)")
TOPIC_GENERIC_ROOT_TOKENS = {
    "פינויים",
    "בטיחות",
    "בדרכים",
    "מאבק",
    "נגע",
    "סמים",
    "מסוכנים",
    "תחבורה",
    "תנועה",
    "עיר",
}

TOPIC_HINT_HIGH_SCORE_MIN = 0.58
TOPIC_TREE_CHILD_SCORE_MIN = 0.3
TOPIC_TREE_SIBLING_FALLBACK_MIN = 0.12
TOPIC_TREE_MAX_CHILDREN = 20
TOPIC_MODEL_SUBTOPIC_CONFIDENCE_MIN = 0.6
TOPIC_FALLBACK_CHILD_LABEL = "החלטה כללית"


@dataclass(slots=True)
class RagCitation:
    chunk_id: str
    source_kind: str
    citation_label: str | None
    document_id: int
    document_title: str
    document_url: str
    start_page: int | None
    end_page: int | None
    score: float


@dataclass(slots=True)
class RagAnswerResult:
    status: str
    answer: str | None
    extended_answer: str | None
    answer_sections: list[dict[str, Any]]
    citations: list[RagCitation]
    claim_assessments: list[dict[str, Any]]
    limitations: list[str]
    extended_answer_sections: list[dict[str, Any]] = field(default_factory=list)
    refusal_reason_code: str | None = None
    refusal_message_he: str | None = None
    missing_source_kinds: list[str] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    scoring: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _AnswerClaim:
    text: str
    citation_chunk_ids: list[str]
    supported: bool | None = None
    best_decision_line_id: str | None = None
    semantic_similarity_score: float | None = None
    semantic_rationale: str | None = None


@dataclass(slots=True)
class _AnswerDraft:
    answer: str
    claims: list[_AnswerClaim]
    limitations: list[str]
    decision_items: list[dict[str, Any]] = field(default_factory=list)
    all_supported_hint: bool | None = None


@dataclass(slots=True)
class _VerificationDraft:
    all_supported: bool
    unsupported_count: int
    claim_support: list[bool] = field(default_factory=list)
    semantic_claims: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class LocalVerifyResult:
    provider: str
    model: str
    text: str | None
    error_code: str | None
    error_text: str | None


@dataclass(slots=True)
class VerifyFallbackCallResult:
    provider: str
    model: str
    text: str | None
    error_code: str | None
    error_text: str | None
    route: str
    external_call_delta: int


@dataclass(slots=True)
class _DecisionLine:
    decision_line_id: str
    chunk_id: str
    source_kind: str
    citation: str | None
    text: str


@dataclass(slots=True)
class _ClaimAssessment:
    text: str
    citation_chunk_ids: list[str]
    score: float
    score_band: str
    matched_decision_line_id: str | None
    matched_decision_text: str | None
    semantic_similarity_score: float | None
    semantic_rationale: str | None
    semantic_source: str
    topic_hits: list[str]
    support_hits: list[str]
    secondary_context_hits: list[str]
    claim_topic_coverage: float
    evidence_topic_coverage: float
    selected_for_answer: bool = True
    exclusion_reason: str | None = None


@dataclass(slots=True)
class _DecisionEvidenceLine:
    text: str
    chunk_id: str


class RagAnsweringService:
    def __init__(self, *, llm_client: RagLlmClient):
        self.llm_client = llm_client

    def compose(
        self,
        *,
        question: str,
        retrieval: RagRetrievalResult,
        required_source_kinds: list[str] | None = None,
        cached_topic_tree: dict[str, list[str]] | None = None,
        protocol_subject_anchors: dict[int, list[str]] | None = None,
        protocol_semantic_topic_labels: dict[int, list[str]] | None = None,
        ask_request_id: str | None = None,
    ) -> RagAnswerResult:
        compose_started = time.perf_counter()
        required_sources = _normalize_source_kinds(required_source_kinds or retrieval.requested_source_kinds)
        log_rag_event(
            "rag.answering.start",
            ask_request_id=ask_request_id,
            retrieval_set_id=retrieval.retrieval_set_id,
            required_source_types=required_sources,
            retrieved_source_types=sorted(retrieval.source_kinds),
            retrieval_context_count=len(retrieval.contexts),
        )

        reason_code, missing_sources = _insufficient_evidence_reason(
            contexts=retrieval.contexts,
            required_source_kinds=required_sources,
        )
        if reason_code is not None:
            return self._build_refusal(
                question=question,
                retrieval=retrieval,
                reason_code=reason_code,
                missing_source_kinds=missing_sources,
                ask_request_id=ask_request_id,
            )

        answer_call_started = time.perf_counter()
        answer_call = self.llm_client.generate(
            call_type=RAG_CALL_ANSWER,
            instruction=(
                "Answer in Hebrew using only provided evidence. "
                "Return strict JSON object with keys: answer (string), limitations (string[]), "
                "all_supported (boolean), claims (array of objects with text, supported, "
                "citation_chunk_ids, best_decision_line_id, semantic_similarity_score, semantic_rationale), "
                "decision_items (array of objects with summary_he, topic_name_he, topic_root_he, topic_subtopic_he, "
                "topic_confidence, topic_granularity_level, citation_chunk_ids). decision_items should contain concise decision summaries "
                "(up to two sentences each) and must align one-to-one with claims by order and citation_chunk_ids. "
                "topic_root_he is protocol-level theme. topic_subtopic_he must be a self-contained noun phrase "
                "(2-4 words) describing a reusable category-level decision topic (not a mini decision sentence). "
                "Use object-first taxonomy: prefer the decided object/domain (for example 'הסכמים') over committee context. "
                "When decision is about agreement preparation/signing, set topic_root_he='הסכמים' and topic_subtopic_he like 'הסכם רשות לעמותה'. "
                "topic_granularity_level must be an integer 1..10 where 1=very general and 10=very specific. "
                "Do not output vague, dangling, or over-specific fragments with concrete entities, places, dates, or street names. "
                "Never output placeholder labels such as 'ללא תיוג סמנטי'. "
                "Never start or end topic_subtopic_he with relation/preposition words (e.g. לגבי, של, על, עם, בין, עבור). "
                "Bad examples: 'בשנית קמפיין', 'שיתוף הפעולה', 'תמרורים מוארים לגבי', 'הזמנה תקציבית'. "
                "Good examples: 'קמפיין בטיחות בדרכים', 'שיתוף פעולה עם קופות חולים', 'תמרורים מוארים', 'בדיקות בטיחות בבתי ספר', 'פינוי מבני עמותות'. "
                "If uncertain, set topic_subtopic_he to null and topic_confidence below 0.5 instead of low-quality text."
            ),
            payload=_answer_payload(question=question, retrieval=retrieval),
            ask_request_id=ask_request_id,
        )
        answer_call_ms = _elapsed_ms(answer_call_started)
        if answer_call.error_code or not answer_call.text:
            return self._build_refusal(
                question=question,
                retrieval=retrieval,
                reason_code=REASON_ANSWER_GENERATION_FAILED,
                missing_source_kinds=[],
                provider=answer_call.provider,
                model=answer_call.model,
                ask_request_id=ask_request_id,
                scoring={
                    "answer_call_error_code": answer_call.error_code,
                    "answer_call_error_text": answer_call.error_text,
                    "answer_call_empty_text": not bool(answer_call.text),
                    "answer_external_api_called": _is_external_provider_name(answer_call.provider),
                    "timing_ms": {
                        "answer_call": answer_call_ms,
                        "compose_total": _elapsed_ms(compose_started),
                    },
                },
            )

        answer_draft = _parse_answer_draft(answer_call.text)
        if answer_draft is None:
            return self._build_refusal(
                question=question,
                retrieval=retrieval,
                reason_code=REASON_INVALID_ANSWER_FORMAT,
                missing_source_kinds=[],
                provider=answer_call.provider,
                model=answer_call.model,
                ask_request_id=ask_request_id,
            )

        context_by_chunk = {context.chunk_id: context for context in retrieval.contexts}
        citation_repair_count, citation_repair_events = _repair_claim_citations(
            claims=answer_draft.claims,
            context_by_chunk=context_by_chunk,
        )
        valid_claims, used_chunk_ids = _validate_claim_citations(answer_draft.claims, context_by_chunk)
        if not valid_claims:
            return self._build_refusal(
                question=question,
                retrieval=retrieval,
                reason_code=REASON_UNCITED_CLAIMS,
                missing_source_kinds=[],
                provider=answer_call.provider,
                model=answer_call.model,
                ask_request_id=ask_request_id,
                scoring={
                    "citation_id_repair_count": citation_repair_count,
                    "citation_id_repair_events": citation_repair_events,
                },
            )

        topic_mismatch_chunk_ids = _topic_mismatch_chunk_ids(
            question=question,
            used_chunk_ids=used_chunk_ids,
            context_by_chunk=context_by_chunk,
        )
        mismatch_used_count = len(used_chunk_ids)
        mismatch_count = len(topic_mismatch_chunk_ids)
        mismatch_ratio = _round_score(mismatch_count / mismatch_used_count) if mismatch_used_count else 0.0
        topic_mismatch_scoring = {
            "topic_mismatch_threshold_rule": "reject if mismatch_count > 0",
            "topic_mismatch_used_count": mismatch_used_count,
            "topic_mismatch_count": mismatch_count,
            "topic_mismatch_ratio": mismatch_ratio,
            "topic_mismatch_chunk_ids": topic_mismatch_chunk_ids,
            "topic_mismatch_chunks": _topic_mismatch_rows(
                chunk_ids=topic_mismatch_chunk_ids,
                context_by_chunk=context_by_chunk,
            ),
        }
        if topic_mismatch_chunk_ids:
            log_rag_event(
                "rag.answering.topic_mismatch",
                ask_request_id=ask_request_id,
                retrieval_set_id=retrieval.retrieval_set_id,
                mismatch_chunk_ids=topic_mismatch_chunk_ids,
                mismatch_count=mismatch_count,
                mismatch_used_count=mismatch_used_count,
                mismatch_ratio=mismatch_ratio,
            )
            return self._build_refusal(
                question=question,
                retrieval=retrieval,
                reason_code=REASON_TOPIC_MISMATCH_EVIDENCE,
                missing_source_kinds=[],
                provider=answer_call.provider,
                model=answer_call.model,
                ask_request_id=ask_request_id,
                scoring=topic_mismatch_scoring,
            )

        decision_lines = _extract_decision_lines(retrieval.contexts)
        decision_line_by_id = {line.decision_line_id: line for line in decision_lines}
        decision_lines_by_chunk = _build_decision_lines_by_chunk(
            contexts=retrieval.contexts,
            decision_lines=decision_lines,
        )

        verify_routing = RagVerifyRoutingConfig.from_env()
        external_call_count = 1 if _is_external_provider_name(answer_call.provider) else 0
        answer_external_api_called = _is_external_provider_name(answer_call.provider)
        verification_provider = answer_call.provider
        verification_model = answer_call.model
        verify_route = "deterministic_only"

        deterministic_similarity_started = time.perf_counter()
        claim_assessments = _assess_claims(
            question=question,
            claims=answer_draft.claims,
            context_by_chunk=context_by_chunk,
            decision_lines_by_chunk=decision_lines_by_chunk,
        )
        deterministic_similarity_ms = _elapsed_ms(deterministic_similarity_started)
        low_score_claim_indices = [
            idx
            for idx, item in enumerate(claim_assessments)
            if item.score < verify_routing.deterministic_low_score_threshold
        ]
        low_score_claims = [claim_assessments[idx] for idx in low_score_claim_indices]
        deterministic_would_refuse = bool(low_score_claim_indices)

        fallback_verify_attempted = False
        fallback_provider_used = "none"
        fallback_all_supported: bool | None = None
        fallback_supported_low_claim_indices: list[int] = []
        fallback_overrode_deterministic_low = False
        fallback_error_code: str | None = None
        fallback_verify_ms: float | None = None

        selected_claim_assessments = _select_claims_for_answer(claim_assessments)
        dropped_claim_assessments = [item for item in claim_assessments if not item.selected_for_answer]
        if not selected_claim_assessments and claim_assessments:
            claim_assessments[0].selected_for_answer = True
            claim_assessments[0].exclusion_reason = None
            selected_claim_assessments = [claim_assessments[0]]
            dropped_claim_assessments = [item for item in claim_assessments[1:] if not item.selected_for_answer]
        selected_used_chunk_ids = _chunk_ids_from_claim_assessments(selected_claim_assessments)

        decision_claim_count = sum(
            1 for item in selected_claim_assessments if _has_decision_marker(normalize_for_search(item.text))
        )
        discussion_claim_count = sum(
            1 for item in selected_claim_assessments if _has_discussion_marker(normalize_for_search(item.text))
        )
        decision_required = _should_require_decision_claims(question)
        decision_reconstruction_used = False
        reconstructed_decision_lines: list[_DecisionEvidenceLine] = []
        enforce_protocol_coverage = _should_enforce_protocol_coverage(question=question, retrieval_contexts=retrieval.contexts)
        protocol_coverage_lines: list[_DecisionEvidenceLine] = []
        protocol_coverage_document_ids: list[int] = []

        def _scoring_payload() -> dict[str, Any]:
            return {
                **topic_mismatch_scoring,
                "claim_score_thresholds": {
                    "high_min": CLAIM_SCORE_HIGH_MIN,
                    "medium_min": CLAIM_SCORE_MEDIUM_MIN,
                    "low_warning_below": LOW_SCORE_WARNING_LIMIT,
                },
                "claim_count": len(claim_assessments),
                "low_score_claim_count": len(low_score_claims),
                "low_score_warning_applied": bool(low_score_claims),
                "semantic_scoring_source": DETERMINISTIC_SIMILARITY_SOURCE,
                "semantic_fallback_used": False,
                "deterministic_low_score_threshold": verify_routing.deterministic_low_score_threshold,
                "deterministic_would_refuse": deterministic_would_refuse,
                "deterministic_low_score_claim_indices": list(low_score_claim_indices),
                "selected_claim_count": len(selected_claim_assessments),
                "dropped_claim_count": len(dropped_claim_assessments),
                "dropped_claim_reasons": [
                    {
                        "text": item.text,
                        "reason": item.exclusion_reason,
                    }
                    for item in dropped_claim_assessments
                ],
                "decision_required": decision_required,
                "decision_claim_count": decision_claim_count,
                "decision_claim_count_discussion": discussion_claim_count,
                "decision_gate_basis": "strong_markers_only",
                "decision_gate_passed": bool(decision_claim_count or decision_reconstruction_used),
                "decision_reconstruction_used": decision_reconstruction_used,
                "decision_reconstruction_count": len(reconstructed_decision_lines),
                "protocol_coverage_enforced": enforce_protocol_coverage,
                "protocol_coverage_augmented_count": len(protocol_coverage_lines),
                "protocol_coverage_augmented_document_ids": list(protocol_coverage_document_ids),
                "retrieval_protocol_document_ids": _protocol_document_ids_from_contexts(retrieval.contexts),
                "selected_protocol_document_ids": _protocol_document_ids_from_chunk_ids(
                    selected_used_chunk_ids,
                    context_by_chunk,
                ),
                "verify_route": verify_route,
                "fallback_verify_attempted": fallback_verify_attempted,
                "fallback_provider": fallback_provider_used,
                "fallback_all_supported": fallback_all_supported,
                "fallback_supported_low_claim_indices": list(fallback_supported_low_claim_indices),
                "fallback_overrode_deterministic_low": fallback_overrode_deterministic_low,
                "fallback_error_code": fallback_error_code,
                "answer_external_api_called": answer_external_api_called,
                "similarity_external_api_called": verify_route == "external_verify_fallback",
                "external_call_count": external_call_count,
                "citation_id_repair_count": citation_repair_count,
                "citation_id_repair_events": citation_repair_events,
                "timing_ms": {
                    "answer_call": answer_call_ms,
                    "deterministic_similarity": deterministic_similarity_ms,
                    "fallback_verify": fallback_verify_ms,
                    "compose_total": _elapsed_ms(compose_started),
                },
            }

        if deterministic_would_refuse:
            fallback_verify_attempted = True
            fallback_provider_used = verify_routing.fallback_provider
            fallback_verify_started = time.perf_counter()
            verify_call = _run_verify_fallback_call(
                llm_client=self.llm_client,
                config=verify_routing,
                question=question,
                answer_draft=answer_draft,
                retrieval=retrieval,
                decision_lines=decision_lines,
                ask_request_id=ask_request_id,
            )
            fallback_verify_ms = _elapsed_ms(fallback_verify_started)
            verify_route = verify_call.route
            verification_provider = verify_call.provider
            verification_model = verify_call.model
            external_call_count += verify_call.external_call_delta
            fallback_error_code = verify_call.error_code

            if verify_call.error_code or not verify_call.text:
                return self._build_refusal(
                    question=question,
                    retrieval=retrieval,
                    reason_code=REASON_VERIFICATION_FAILED,
                    missing_source_kinds=[],
                    provider=verify_call.provider,
                    model=verify_call.model,
                    ask_request_id=ask_request_id,
                    scoring=_scoring_payload(),
                )

            verification = _parse_verification(
                verify_call.text,
                context_by_chunk,
                decision_line_by_id=decision_line_by_id,
            )
            if verification is None:
                return self._build_refusal(
                    question=question,
                    retrieval=retrieval,
                    reason_code=REASON_INVALID_VERIFICATION_FORMAT,
                    missing_source_kinds=[],
                    provider=verify_call.provider,
                    model=verify_call.model,
                    ask_request_id=ask_request_id,
                    scoring=_scoring_payload(),
                )

            fallback_all_supported = verification.all_supported
            fallback_supported_low_claim_indices = [
                idx
                for idx in low_score_claim_indices
                if idx < len(verification.claim_support) and verification.claim_support[idx]
            ]
            fallback_overrode_deterministic_low = (
                verification.all_supported and bool(fallback_supported_low_claim_indices)
            )

            if not verification.all_supported:
                return self._build_refusal(
                    question=question,
                    retrieval=retrieval,
                    reason_code=REASON_UNSUPPORTED_CLAIMS,
                    missing_source_kinds=[],
                    provider=verification_provider,
                    model=verification_model,
                    ask_request_id=ask_request_id,
                    scoring=_scoring_payload(),
                )

        if decision_required and decision_claim_count == 0:
            reconstructed_decision_lines = _reconstruct_decision_lines(
                question=question,
                retrieval_contexts=retrieval.contexts,
                selected_claim_assessments=selected_claim_assessments,
                context_by_chunk=context_by_chunk,
                decision_lines_by_chunk=decision_lines_by_chunk,
            )
            if reconstructed_decision_lines:
                decision_reconstruction_used = True
                selected_used_chunk_ids = _chunk_ids_from_decision_lines(reconstructed_decision_lines)
            else:
                return self._build_refusal(
                    question=question,
                    retrieval=retrieval,
                    reason_code=REASON_NO_DECISION_CONTENT,
                    missing_source_kinds=[],
                    provider=verification_provider,
                    model=verification_model,
                    ask_request_id=ask_request_id,
                    scoring=_scoring_payload(),
                )

        if enforce_protocol_coverage:
            protocol_coverage_lines = _missing_protocol_coverage_lines(
                question=question,
                retrieval_contexts=retrieval.contexts,
                selected_chunk_ids=selected_used_chunk_ids,
                context_by_chunk=context_by_chunk,
                decision_lines_by_chunk=decision_lines_by_chunk,
            )
            if protocol_coverage_lines:
                selected_used_chunk_ids = _merge_chunk_ids(
                    selected_used_chunk_ids,
                    _chunk_ids_from_decision_lines(protocol_coverage_lines),
                )
                protocol_coverage_document_ids = _protocol_document_ids_from_decision_lines(
                    protocol_coverage_lines,
                    context_by_chunk,
                )

        citations = _build_citations(retrieval.contexts, selected_used_chunk_ids)
        if not citations:
            return self._build_refusal(
                question=question,
                retrieval=retrieval,
                reason_code=REASON_UNCITED_CLAIMS,
                missing_source_kinds=[],
                provider=verification_provider,
                model=verification_model,
                ask_request_id=ask_request_id,
            )

        missing_sources = sorted(set(required_sources) - {citation.source_kind for citation in citations})
        if missing_sources:
            return self._build_refusal(
                question=question,
                retrieval=retrieval,
                reason_code=_reason_code_for_missing_sources(missing_sources),
                missing_source_kinds=missing_sources,
                provider=verification_provider,
                model=verification_model,
                ask_request_id=ask_request_id,
            )

        limitations = list(answer_draft.limitations or ["התשובה מוגבלת לקטעי הראיות שסופקו."])
        if low_score_claims:
            limitations.append(
                f"אזהרה: {len(low_score_claims)} טענות בעלות התאמה נמוכה לשאלה. מומלץ לאמת אותן מול המסמכים המקוריים."
            )
        if fallback_overrode_deterministic_low:
            limitations.append(
                "אימות fallback אישר טענות שסומנו כנמוכות בציון הדטרמיניסטי. בדוק את debug.scoring לפרטים."
            )
        if dropped_claim_assessments:
            limitations.append(
                f"הושמטו {len(dropped_claim_assessments)} טענות משניות שלא עמדו ברלוונטיות ליבת השאלה."
            )
        if decision_reconstruction_used:
            limitations.append("הנוסח הורכב משורות החלטה שחולצו דטרמיניסטית מהראיות.")
        if protocol_coverage_lines:
            limitations.append("בוצעה השלמת כיסוי פרוטוקולים דטרמיניסטית כדי למנוע השמטת מסמכים שנשלפו.")

        scoring_payload = _scoring_payload()
        raw_model_answer = answer_draft.answer.strip()
        summary_items = _decision_summary_items_for_answer(
            question=question,
            answer_draft=answer_draft,
            selected_claim_assessments=selected_claim_assessments,
            reconstructed_decision_lines=reconstructed_decision_lines,
            decision_reconstruction_used=decision_reconstruction_used,
            protocol_coverage_lines=protocol_coverage_lines,
            context_by_chunk=context_by_chunk,
        )
        answer_sections, extended_answer_sections = _build_answer_sections(
            summary_items=summary_items,
            context_by_chunk=context_by_chunk,
            fallback_topic=_default_topic_name(question),
            cached_topic_tree=cached_topic_tree,
            protocol_subject_anchors=protocol_subject_anchors,
        )
        (
            answer_sections,
            extended_answer_sections,
            broad_protocol_split_applied,
        ) = _expand_sections_by_protocol_for_broad_query(
            question=question,
            answer_sections=answer_sections,
            extended_answer_sections=extended_answer_sections,
            context_by_chunk=context_by_chunk,
        )
        (
            answer_sections,
            extended_answer_sections,
            semantic_topic_enforced,
            broad_duplicate_text_fixed,
        ) = _enforce_semantic_topics_and_section_uniqueness(
            question=question,
            answer_sections=answer_sections,
            extended_answer_sections=extended_answer_sections,
            context_by_chunk=context_by_chunk,
            protocol_semantic_topic_labels=protocol_semantic_topic_labels,
        )
        selected_used_chunk_ids = _merge_chunk_ids(
            selected_used_chunk_ids,
            _section_chunk_ids(answer_sections, extended_answer_sections),
        )
        citations = _build_citations(retrieval.contexts, selected_used_chunk_ids)
        scoring_payload["topic_assignment_routes"] = [
            _as_optional_str(section.get("topic_route")) or "unknown"
            for section in answer_sections
        ]
        scoring_payload["topic_assignment_scores"] = [
            _as_float_0_1(section.get("topic_score"))
            for section in answer_sections
        ]
        scoring_payload["topic_tree_cached_protocol_count"] = len(cached_topic_tree or {})
        scoring_payload["topic_tree_cached_child_count"] = sum(len(children) for children in (cached_topic_tree or {}).values())
        scoring_payload["topic_subject_anchor_document_count"] = len(protocol_subject_anchors or {})
        scoring_payload["broad_query_protocol_split_applied"] = broad_protocol_split_applied
        scoring_payload["semantic_topic_enforced"] = semantic_topic_enforced
        scoring_payload["broad_duplicate_text_fixed"] = broad_duplicate_text_fixed
        final_answer = _compose_answer_from_sections(answer_sections)
        extended_answer = _compose_answer_from_sections(extended_answer_sections)
        if not extended_answer:
            extended_answer = raw_model_answer or final_answer
        if _should_use_extended_answer_as_concise(
            answer_draft=answer_draft,
            answer_sections=answer_sections,
            extended_answer=extended_answer,
        ):
            final_answer = raw_model_answer or extended_answer
            if raw_model_answer:
                extended_answer = raw_model_answer
        if not final_answer:
            final_answer = _compose_answer_from_claim_assessments(selected_claim_assessments)

        result = RagAnswerResult(
            status="answer",
            answer=final_answer,
            extended_answer=extended_answer,
            answer_sections=answer_sections,
            extended_answer_sections=extended_answer_sections,
            citations=citations,
            claim_assessments=[_claim_assessment_payload(item) for item in claim_assessments],
            limitations=limitations,
            provider=verification_provider,
            model=verification_model,
            scoring=scoring_payload,
        )
        log_rag_event(
            "rag.answering.answer",
            ask_request_id=ask_request_id,
            retrieval_set_id=retrieval.retrieval_set_id,
            citation_count=len(result.citations),
            citation_chunk_ids=[citation.chunk_id for citation in result.citations],
            limitation_count=len(result.limitations),
            provider=result.provider,
            model=result.model,
            required_source_types=required_sources,
            covered_source_types=sorted({citation.source_kind for citation in result.citations}),
            claim_count=len(claim_assessments),
            low_score_claim_count=len(low_score_claims),
        )
        return result

    def _build_refusal(
        self,
        *,
        question: str,
        retrieval: RagRetrievalResult,
        reason_code: str,
        missing_source_kinds: list[str],
        provider: str | None = None,
        model: str | None = None,
        ask_request_id: str | None = None,
        scoring: dict[str, Any] | None = None,
    ) -> RagAnswerResult:
        refusal_call = self.llm_client.generate(
            call_type=RAG_CALL_REFUSE,
            instruction=(
                "Decide refusal only. Return strict JSON object with keys: "
                "refusal_message_he and missing_source_kinds."
            ),
            payload={
                "question": question,
                "reason_code": reason_code,
                "missing_source_kinds": missing_source_kinds,
                "retrieved_contexts": [
                    {
                        "chunk_id": context.chunk_id,
                        "source_kind": context.source_kind,
                        "citation": context.citation,
                    }
                    for context in retrieval.contexts
                ],
            },
            ask_request_id=ask_request_id,
        )

        refusal_message = _hebrew_refusal_message(
            reason_code=reason_code,
            missing_source_kinds=missing_source_kinds,
        )

        result = RagAnswerResult(
            status="refusal",
            answer=None,
            extended_answer=None,
            answer_sections=[],
            extended_answer_sections=[],
            citations=[],
            claim_assessments=[],
            limitations=["אין להשיב ללא ראיות מספקות."],
            refusal_reason_code=reason_code,
            refusal_message_he=refusal_message,
            missing_source_kinds=missing_source_kinds,
            provider=provider or refusal_call.provider,
            model=model or refusal_call.model,
            scoring=dict(scoring or {}),
        )
        log_rag_event(
            "rag.answering.refusal",
            ask_request_id=ask_request_id,
            retrieval_set_id=retrieval.retrieval_set_id,
            reason_code=reason_code,
            missing_source_types=missing_source_kinds,
            retrieval_context_count=len(retrieval.contexts),
            retrieved_source_types=sorted({context.source_kind for context in retrieval.contexts}),
            provider=result.provider,
            model=result.model,
            refusal_call_error_code=refusal_call.error_code,
        )
        return result


def _answer_payload(*, question: str, retrieval: RagRetrievalResult) -> dict[str, Any]:
    decision_lines = _extract_decision_lines(retrieval.contexts)
    return {
        "question": question,
        "contexts": [
            {
                "chunk_id": context.chunk_id,
                "source_kind": context.source_kind,
                "citation": context.citation,
                "document_id": context.document_id,
                "document_title": context.document_title,
                "snippet": context.snippet,
            }
            for context in retrieval.contexts
        ],
        "decision_lines": [
            {
                "decision_line_id": row.decision_line_id,
                "chunk_id": row.chunk_id,
                "source_kind": row.source_kind,
                "citation": row.citation,
                "text": row.text,
            }
            for row in decision_lines
        ],
        "rules": [
            "Use only listed contexts",
            "Every major claim must include citation_chunk_ids",
            "Do not output claims without citation_chunk_ids",
            "Set supported per claim and set all_supported=false if any claim is unsupported",
            "For every claim, map best_decision_line_id from decision_lines when available",
            "Set semantic_similarity_score between 0 and 1 for question-to-decision topical relevance",
            "Return decision_items with concise decision summaries (up to 2 sentences), each with citation_chunk_ids",
            "For each decision_item also return topic_root_he, topic_subtopic_he, topic_confidence (0..1), and topic_granularity_level (1..10)",
            "topic_root_he must be object/domain-first (for example הסכמים, הקצאות, תמרורים) and not committee-label-first",
            "If decision concerns preparing/signing an agreement, use topic_root_he='הסכמים' and topic_subtopic_he similar to 'הסכם רשות לעמותה'",
            "decision_items must align one-to-one with claims by order and citation_chunk_ids",
            "topic_subtopic_he must be a standalone noun phrase (2-4 words), category-level and not concrete-entity heavy",
            "topic_subtopic_he must never be a mini decision sentence or include location/date/entity specifics",
            "topic_subtopic_he must never be an administrative shell label without subject (for example 'הזמנה תקציבית')",
            "topic_subtopic_he must never start/end with relational words",
            "If topic_subtopic_he is uncertain set it to null and topic_confidence below 0.5",
            "topic_granularity_level uses: 1=very general category, 10=very specific case detail",
            "Never emit placeholder topic labels like 'ללא תיוג סמנטי'",
        ],
    }


def _verification_payload(
    *,
    question: str,
    answer_draft: _AnswerDraft,
    retrieval: RagRetrievalResult,
    decision_lines: list[_DecisionLine],
) -> dict[str, Any]:
    return {
        "question": question,
        "answer": answer_draft.answer,
        "claims": [
            {
                "text": claim.text,
                "citation_chunk_ids": claim.citation_chunk_ids,
            }
            for claim in answer_draft.claims
        ],
        "contexts": [
            {
                "chunk_id": context.chunk_id,
                "source_kind": context.source_kind,
                "citation": context.citation,
                "document_title": context.document_title,
                "snippet": context.snippet,
            }
            for context in retrieval.contexts
        ],
        "decision_lines": [
            {
                "decision_line_id": row.decision_line_id,
                "chunk_id": row.chunk_id,
                "source_kind": row.source_kind,
                "citation": row.citation,
                "text": row.text,
            }
            for row in decision_lines
        ],
        "rules": [
            "Mark supported true only if citations back the exact claim",
            "If any claim is unsupported set all_supported=false",
            "For every claim, map to best_decision_line_id from decision_lines",
            "Set semantic_similarity_score between 0 and 1 using question and matched decision line semantics",
            "Use this rubric for semantic_similarity_score (question-to-decision relevance): 0.90-1.00 core direct road-safety/traffic control decision; 0.65-0.85 related traffic-safety operational decision; 0.35-0.64 adjacent safety context (for example school-area safety due to transport context); below 0.35 unrelated",
            "Do not assign 0.95+ unless the matched decision is central to the exact question topic",
            "semantic_similarity_score is for topic centrality, not textual paraphrase quality",
        ],
    }


def _verification_instruction() -> str:
    return (
        "Verify every claim against provided evidence. "
        "Return strict JSON object with keys: all_supported (boolean), "
        "claims (array of objects with text, supported, citation_chunk_ids, "
        "best_decision_line_id, semantic_similarity_score, semantic_rationale). "
        "semantic_similarity_score must reflect question-to-decision topical relevance, "
        "not only claim-to-decision wording similarity."
    )


def _verification_from_answer_claims(
    *,
    answer_draft: _AnswerDraft,
    context_by_chunk: dict[str, RagContextChunk],
    decision_line_by_id: dict[str, _DecisionLine],
) -> _VerificationDraft | None:
    if not answer_draft.claims:
        return None

    if any(claim.supported is None for claim in answer_draft.claims):
        return None

    unsupported_count = 0
    claim_support: list[bool] = []
    semantic_claims: list[dict[str, Any]] = []
    for claim in answer_draft.claims:
        if any(chunk_id not in context_by_chunk for chunk_id in claim.citation_chunk_ids):
            return None
        if claim.supported is None:
            return None
        if claim.supported is False:
            unsupported_count += 1
        claim_support.append(claim.supported)

        if (
            claim.best_decision_line_id
            and claim.semantic_similarity_score is not None
            and claim.best_decision_line_id in decision_line_by_id
        ):
            semantic_claims.append(
                {
                    "text": claim.text,
                    "citation_chunk_ids": list(claim.citation_chunk_ids),
                    "best_decision_line_id": claim.best_decision_line_id,
                    "semantic_similarity_score": claim.semantic_similarity_score,
                    "semantic_rationale": claim.semantic_rationale,
                }
            )

    all_supported = unsupported_count == 0
    if answer_draft.all_supported_hint is not None and answer_draft.all_supported_hint != all_supported:
        return None

    return _VerificationDraft(
        all_supported=all_supported,
        unsupported_count=unsupported_count,
        claim_support=claim_support,
        semantic_claims=semantic_claims,
    )


def _run_local_verify(
    *,
    question: str,
    answer_draft: _AnswerDraft,
    retrieval: RagRetrievalResult,
    decision_lines: list[_DecisionLine],
    config: RagVerifyRoutingConfig,
) -> LocalVerifyResult:
    verify_payload = _verification_payload(
        question=question,
        answer_draft=answer_draft,
        retrieval=retrieval,
        decision_lines=decision_lines,
    )
    prompt = (
        "Return only strict JSON (no markdown) with keys all_supported and claims. "
        "Each claim must include text, supported, citation_chunk_ids, best_decision_line_id, "
        "semantic_similarity_score, semantic_rationale.\n"
        f"Input:\n{json.dumps(verify_payload, ensure_ascii=False)}"
    )

    model_dir = _resolve_local_model_dir(config.local_model_dir)
    command = [
        sys.executable,
        "-m",
        "local_llm.tinyllama_local",
        "--prompt",
        prompt,
        "--model-dir",
        model_dir,
        "--device",
        config.local_device,
        "--max-new-tokens",
        str(config.local_max_new_tokens),
        "--json",
    ]

    started = time.perf_counter()
    try:
        process = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=config.local_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return LocalVerifyResult(
            provider=LOCAL_VERIFY_PROVIDER_NAME,
            model=model_dir,
            text=None,
            error_code="LOCAL_VERIFY_TIMEOUT",
            error_text=f"timeout after {config.local_timeout_seconds}s",
        )

    latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
    if process.returncode != 0:
        stderr = (process.stderr or "").strip() or None
        return LocalVerifyResult(
            provider=LOCAL_VERIFY_PROVIDER_NAME,
            model=model_dir,
            text=None,
            error_code="LOCAL_VERIFY_PROCESS_FAILED",
            error_text=stderr or f"exit code {process.returncode}",
        )

    stdout = (process.stdout or "").strip()
    if not stdout:
        return LocalVerifyResult(
            provider=LOCAL_VERIFY_PROVIDER_NAME,
            model=model_dir,
            text=None,
            error_code="LOCAL_VERIFY_EMPTY_STDOUT",
            error_text="empty local verifier output",
        )

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return LocalVerifyResult(
            provider=LOCAL_VERIFY_PROVIDER_NAME,
            model=model_dir,
            text=stdout,
            error_code="LOCAL_VERIFY_INVALID_JSON",
            error_text="local verifier did not return JSON",
        )

    if not isinstance(payload, dict):
        return LocalVerifyResult(
            provider=LOCAL_VERIFY_PROVIDER_NAME,
            model=model_dir,
            text=None,
            error_code="LOCAL_VERIFY_INVALID_PAYLOAD",
            error_text="local verifier output is not an object",
        )

    response_text = _as_optional_str(payload.get("response_text"))
    model_name = _as_optional_str(payload.get("model_source")) or model_dir
    if not response_text:
        return LocalVerifyResult(
            provider=LOCAL_VERIFY_PROVIDER_NAME,
            model=model_name,
            text=None,
            error_code="LOCAL_VERIFY_EMPTY_RESPONSE",
            error_text="missing response_text in local verifier output",
        )

    log_rag_event(
        "rag.answering.local_verify.result",
        call_type=RAG_CALL_VERIFY,
        provider=LOCAL_VERIFY_PROVIDER_NAME,
        model=model_name,
        latency_ms=latency_ms,
    )
    return LocalVerifyResult(
        provider=LOCAL_VERIFY_PROVIDER_NAME,
        model=model_name,
        text=response_text,
        error_code=None,
        error_text=None,
    )


def _resolve_local_model_dir(value: str) -> str:
    compact = value.strip()
    if not compact:
        compact = LOCAL_VERIFY_MODEL_DIR_DEFAULT
    path = Path(compact).expanduser()
    if path.is_absolute():
        return str(path)
    project_root = Path(__file__).resolve().parents[2]
    return str((project_root / path).resolve())


def _is_external_provider_name(provider_name: str | None) -> bool:
    if not provider_name:
        return False
    return provider_name.strip().casefold() == BYTEZ_PROVIDER.casefold()


def _parse_answer_draft(value: str) -> _AnswerDraft | None:
    payload = _parse_json_object(value)
    if payload is None:
        return None

    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return None

    claims_payload = payload.get("claims")
    if not isinstance(claims_payload, list) or not claims_payload:
        return None

    all_supported_hint_raw = payload.get("all_supported")
    all_supported_hint = all_supported_hint_raw if isinstance(all_supported_hint_raw, bool) else None

    claims: list[_AnswerClaim] = []
    for claim_payload in claims_payload:
        if not isinstance(claim_payload, dict):
            return None

        claim_text = claim_payload.get("text")
        if not isinstance(claim_text, str) or not claim_text.strip():
            claim_text = claim_payload.get("claim")
        if not isinstance(claim_text, str) or not claim_text.strip():
            return None

        citation_chunk_ids = claim_payload.get("citation_chunk_ids")
        if citation_chunk_ids is None:
            citation_chunk_ids = claim_payload.get("chunk_ids")
        if not isinstance(citation_chunk_ids, list):
            return None
        normalized_ids = _normalize_chunk_ids(citation_chunk_ids)
        if not normalized_ids:
            return None

        supported_raw = claim_payload.get("supported")
        supported = supported_raw if isinstance(supported_raw, bool) else None

        best_decision_line_id = _as_optional_str(claim_payload.get("best_decision_line_id"))
        semantic_similarity_score = _as_float_0_1(claim_payload.get("semantic_similarity_score"))
        semantic_rationale = _as_optional_str(claim_payload.get("semantic_rationale"))

        claims.append(
            _AnswerClaim(
                text=claim_text.strip(),
                citation_chunk_ids=normalized_ids,
                supported=supported,
                best_decision_line_id=best_decision_line_id,
                semantic_similarity_score=semantic_similarity_score,
                semantic_rationale=semantic_rationale,
            )
        )

    limitations = payload.get("limitations")
    if isinstance(limitations, str):
        limitations_list = [limitations.strip()] if limitations.strip() else []
    elif isinstance(limitations, list):
        limitations_list = [item.strip() for item in limitations if isinstance(item, str) and item.strip()]
    else:
        limitations_list = []

    decision_items = _parse_decision_items(payload.get("decision_items"))

    return _AnswerDraft(
        answer=answer.strip(),
        claims=claims,
        limitations=limitations_list,
        decision_items=decision_items,
        all_supported_hint=all_supported_hint,
    )


def _parse_decision_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary_he")
        if not isinstance(summary, str) or not summary.strip():
            continue

        topic_name = _as_optional_str(item.get("topic_name_he"))
        topic_root = _as_optional_str(item.get("topic_root_he"))
        topic_subtopic = _as_optional_str(item.get("topic_subtopic_he"))
        topic_confidence = _as_float_0_1(item.get("topic_confidence"))
        topic_granularity_level = _as_int_in_range(item.get("topic_granularity_level"), min_value=1, max_value=10)
        citation_chunk_ids = item.get("citation_chunk_ids")
        if not isinstance(citation_chunk_ids, list):
            continue
        normalized_ids = _normalize_chunk_ids(citation_chunk_ids)
        if not normalized_ids:
            continue

        if topic_root is None and topic_name is not None:
            topic_root = topic_name

        items.append(
            {
                "summary_he": summary.strip(),
                "topic_name_he": topic_name,
                "topic_root_he": topic_root,
                "topic_subtopic_he": topic_subtopic,
                "topic_confidence": topic_confidence,
                "topic_granularity_level": topic_granularity_level,
                "citation_chunk_ids": normalized_ids,
            }
        )

    return items


def _validate_claim_citations(
    claims: list[_AnswerClaim],
    context_by_chunk: dict[str, RagContextChunk],
) -> tuple[bool, list[str]]:
    used_chunk_ids: list[str] = []
    seen: set[str] = set()

    for claim in claims:
        if not claim.citation_chunk_ids:
            return False, []
        for chunk_id in claim.citation_chunk_ids:
            if chunk_id not in context_by_chunk:
                return False, []
            if chunk_id not in seen:
                used_chunk_ids.append(chunk_id)
                seen.add(chunk_id)

    return bool(used_chunk_ids), used_chunk_ids


def _repair_claim_citations(
    *,
    claims: list[_AnswerClaim],
    context_by_chunk: dict[str, RagContextChunk],
) -> tuple[int, list[dict[str, Any]]]:
    if not claims or not context_by_chunk:
        return 0, []

    known_chunk_ids = list(context_by_chunk.keys())
    repair_count = 0
    repair_events: list[dict[str, Any]] = []

    for claim_index, claim in enumerate(claims):
        repaired_ids: list[str] = []
        seen: set[str] = set()

        for raw_chunk_id in claim.citation_chunk_ids:
            if raw_chunk_id in context_by_chunk:
                if raw_chunk_id not in seen:
                    repaired_ids.append(raw_chunk_id)
                    seen.add(raw_chunk_id)
                continue

            replacement = _closest_chunk_id(raw_chunk_id, known_chunk_ids)
            if replacement is None:
                if raw_chunk_id not in seen:
                    repaired_ids.append(raw_chunk_id)
                    seen.add(raw_chunk_id)
                continue

            repair_count += 1
            repair_events.append(
                {
                    "claim_index": claim_index,
                    "from_chunk_id": raw_chunk_id,
                    "to_chunk_id": replacement,
                    "reason": "closest_known_chunk_id",
                }
            )
            if replacement not in seen:
                repaired_ids.append(replacement)
                seen.add(replacement)

        claim.citation_chunk_ids = repaired_ids

    return repair_count, repair_events


def _closest_chunk_id(value: str, candidates: list[str]) -> str | None:
    if not value or not candidates:
        return None
    normalized_value = value.strip().lower()
    if not normalized_value:
        return None

    scored: list[tuple[int, str]] = []
    for candidate in candidates:
        distance = _bounded_edit_distance(normalized_value, candidate.lower(), max_distance=2)
        if distance is None:
            continue
        scored.append((distance, candidate))

    if not scored:
        return None

    scored.sort(key=lambda row: row[0])
    best_distance, best_candidate = scored[0]
    if best_distance > 2:
        return None
    if len(scored) > 1 and scored[1][0] == best_distance:
        return None
    return best_candidate


def _bounded_edit_distance(left: str, right: str, *, max_distance: int) -> int | None:
    if abs(len(left) - len(right)) > max_distance:
        return None

    if left == right:
        return 0

    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        row_min = current[0]
        for j, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + cost,
                )
            )
            row_min = min(row_min, current[j])
        if row_min > max_distance:
            return None
        previous = current

    distance = previous[-1]
    if distance > max_distance:
        return None
    return distance


def _parse_verification(
    value: str,
    context_by_chunk: dict[str, RagContextChunk],
    *,
    decision_line_by_id: dict[str, _DecisionLine],
) -> _VerificationDraft | None:
    payload = _parse_json_object(value)
    if payload is None:
        return None

    all_supported_raw = payload.get("all_supported")
    if not isinstance(all_supported_raw, bool):
        return None

    claims_payload = payload.get("claims")
    if not isinstance(claims_payload, list) or not claims_payload:
        return None

    unsupported_count = 0
    claim_support: list[bool] = []
    semantic_claims: list[dict[str, Any]] = []
    for claim in claims_payload:
        if not isinstance(claim, dict):
            return None
        supported = claim.get("supported")
        if not isinstance(supported, bool):
            return None
        claim_support.append(supported)
        if not supported:
            unsupported_count += 1

        citation_chunk_ids = claim.get("citation_chunk_ids")
        if citation_chunk_ids is None:
            citation_chunk_ids = claim.get("chunk_ids")
        if not isinstance(citation_chunk_ids, list):
            return None
        normalized_ids = _normalize_chunk_ids(citation_chunk_ids)
        if not normalized_ids:
            return None
        if any(chunk_id not in context_by_chunk for chunk_id in normalized_ids):
            return None

        decision_line_id = claim.get("best_decision_line_id")
        semantic_similarity_score = claim.get("semantic_similarity_score")
        decision_line_id_norm = decision_line_id.strip() if isinstance(decision_line_id, str) else None
        semantic_score_norm = _as_float_0_1(semantic_similarity_score)
        if decision_line_id_norm and semantic_score_norm is not None and decision_line_id_norm in decision_line_by_id:
            claim_text = claim.get("text")
            semantic_claims.append(
                {
                    "text": claim_text.strip() if isinstance(claim_text, str) and claim_text.strip() else None,
                    "citation_chunk_ids": normalized_ids,
                    "best_decision_line_id": decision_line_id_norm,
                    "semantic_similarity_score": semantic_score_norm,
                    "semantic_rationale": _as_optional_str(claim.get("semantic_rationale")),
                }
            )

    if all_supported_raw and unsupported_count > 0:
        return None

    return _VerificationDraft(
        all_supported=all_supported_raw,
        unsupported_count=unsupported_count,
        claim_support=claim_support,
        semantic_claims=semantic_claims,
    )


def _build_citations(contexts: list[RagContextChunk], used_chunk_ids: list[str]) -> list[RagCitation]:
    used_set = set(used_chunk_ids)
    citations: list[RagCitation] = []
    for context in contexts:
        if context.chunk_id not in used_set:
            continue
        citations.append(
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
            )
        )
    return citations


def _insufficient_evidence_reason(
    *,
    contexts: list[RagContextChunk],
    required_source_kinds: list[str],
) -> tuple[str | None, list[str]]:
    if not contexts:
        return REASON_INSUFFICIENT_EVIDENCE, required_source_kinds

    found_sources = {context.source_kind for context in contexts}
    missing = sorted(set(required_source_kinds) - found_sources)
    if not missing:
        return None, []
    return _reason_code_for_missing_sources(missing), missing


def _reason_code_for_missing_sources(missing_sources: list[str]) -> str:
    missing_set = set(missing_sources)
    if missing_set == {"protocol"}:
        return REASON_MISSING_PROTOCOL_EVIDENCE
    if missing_set == {"attachment"}:
        return REASON_MISSING_ATTACHMENT_EVIDENCE
    return REASON_MISSING_MIXED_SOURCE_EVIDENCE


def _hebrew_refusal_message(*, reason_code: str, missing_source_kinds: list[str]) -> str:
    if reason_code in {
        REASON_MISSING_PROTOCOL_EVIDENCE,
        REASON_MISSING_ATTACHMENT_EVIDENCE,
        REASON_MISSING_MIXED_SOURCE_EVIDENCE,
    }:
        missing_labels = {
            "protocol": "פרוטוקול",
            "attachment": "נספח",
            "other": "מקור נוסף",
        }
        missing_text = ", ".join(missing_labels.get(source_kind, source_kind) for source_kind in missing_source_kinds)
        return (
            "אין מספיק ראיות כדי להשיב באופן מבוסס ציטוטים בלבד. "
            f"חסר מקור מסוג: {missing_text}. "
            "אנא ספק מקורות נוספים או נסח שאלה מצומצמת יותר."
        )

    if reason_code == REASON_UNSUPPORTED_CLAIMS:
        return "אין מספיק ראיות כדי לאמת את כל הטענות בתשובה. לכן אני מסרב להשיב ללא ביסוס מלא."

    if reason_code == REASON_TOPIC_MISMATCH_EVIDENCE:
        return (
            "אין מספיק ראיות ממוקדות לנושא השאלה. "
            "נשלפו מקורות שאינם באותו תחום תוכן, ולכן אני מסרב להשיב תשובה מאוחדת."
        )

    if reason_code in {
        REASON_UNCITED_CLAIMS,
        REASON_INVALID_ANSWER_FORMAT,
        REASON_INVALID_VERIFICATION_FORMAT,
    }:
        return "אין מספיק ראיות מצוטטות לכל הטענות. לכן אני מסרב להשיב כדי למנוע השלמה ספקולטיבית."

    return "אין מספיק ראיות כדי לספק תשובה מבוססת. אנא ספק הקשר נוסף או מקורות תומכים."


def _parse_json_object(value: str) -> dict[str, Any] | None:
    compact = value.strip()
    if not compact:
        return None

    candidates: list[str] = [compact]
    stripped_fence = _strip_markdown_code_fence(compact)
    if stripped_fence and stripped_fence != compact:
        candidates.append(stripped_fence)

    for candidate in candidates:
        payload = _loads_json_object(candidate)
        if payload is not None:
            return payload

        json_slice = _extract_json_object_slice(candidate)
        if json_slice:
            payload = _loads_json_object(json_slice)
            if payload is not None:
                return payload

    return None


def _strip_markdown_code_fence(value: str) -> str:
    stripped = value.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if not lines:
        return stripped

    if lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_object_slice(value: str) -> str | None:
    start = value.find("{")
    end = value.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return value[start : end + 1].strip()


def _loads_json_object(value: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _normalize_chunk_ids(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        out.append(normalized)
        seen.add(normalized)
    return out


def _normalize_source_kinds(source_kinds: list[str]) -> list[str]:
    accepted = {"protocol", "attachment", "other"}
    out: list[str] = []
    seen: set[str] = set()
    for source_kind in source_kinds:
        normalized = (source_kind or "").strip().casefold()
        if normalized in accepted and normalized not in seen:
            out.append(normalized)
            seen.add(normalized)
    return out


def _topic_mismatch_chunk_ids(
    *,
    question: str,
    used_chunk_ids: list[str],
    context_by_chunk: dict[str, RagContextChunk],
) -> list[str]:
    topic_tokens = _primary_topic_tokens(question)
    if not topic_tokens:
        return []

    mismatched: list[str] = []
    for chunk_id in used_chunk_ids:
        context = context_by_chunk.get(chunk_id)
        if context is None:
            continue
        haystack = normalize_for_search(f"{context.document_title} {context.snippet}")
        if not any(token in haystack for token in topic_tokens):
            mismatched.append(chunk_id)
    return mismatched


def _topic_mismatch_rows(
    *,
    chunk_ids: list[str],
    context_by_chunk: dict[str, RagContextChunk],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for chunk_id in chunk_ids:
        context = context_by_chunk.get(chunk_id)
        if context is None:
            continue
        rows.append(
            {
                "chunk_id": chunk_id,
                "source_kind": context.source_kind,
                "citation": context.citation,
                "document_id": context.document_id,
                "document_title": context.document_title,
                "chunk_text": context.chunk_text or context.snippet,
            }
        )
    return rows


def _extract_decision_lines(contexts: list[RagContextChunk]) -> list[_DecisionLine]:
    lines: list[_DecisionLine] = []
    for context in contexts:
        source_text = context.chunk_text or context.snippet
        if not source_text.strip():
            continue
        numbered = list(NUMBERED_DECISION_RE.finditer(source_text))
        if not numbered:
            continue
        for match in numbered:
            number = match.group(1)
            decision_text = " ".join(match.group(2).split())
            if not decision_text:
                continue
            lines.append(
                _DecisionLine(
                    decision_line_id=f"{context.chunk_id}:d{number}",
                    chunk_id=context.chunk_id,
                    source_kind=context.source_kind,
                    citation=context.citation,
                    text=decision_text,
                )
            )
    return lines


def _build_decision_lines_by_chunk(
    *,
    contexts: list[RagContextChunk],
    decision_lines: list[_DecisionLine],
) -> dict[str, list[_DecisionLine]]:
    by_chunk: dict[str, list[_DecisionLine]] = {}
    for row in decision_lines:
        by_chunk.setdefault(row.chunk_id, []).append(row)

    for context in contexts:
        if by_chunk.get(context.chunk_id):
            continue
        fallback_text = " ".join((context.chunk_text or context.snippet or "").split())
        if not fallback_text:
            continue
        by_chunk[context.chunk_id] = [
            _DecisionLine(
                decision_line_id=f"{context.chunk_id}:snippet",
                chunk_id=context.chunk_id,
                source_kind=context.source_kind,
                citation=context.citation,
                text=fallback_text,
            )
        ]

    return by_chunk


def _run_verify_fallback_call(
    *,
    llm_client: RagLlmClient,
    config: RagVerifyRoutingConfig,
    question: str,
    answer_draft: _AnswerDraft,
    retrieval: RagRetrievalResult,
    decision_lines: list[_DecisionLine],
    ask_request_id: str | None,
) -> VerifyFallbackCallResult:
    if config.fallback_provider == VERIFY_FALLBACK_PROVIDER_LOCAL:
        local_result = _run_local_verify(
            question=question,
            answer_draft=answer_draft,
            retrieval=retrieval,
            decision_lines=decision_lines,
            config=config,
        )
        return VerifyFallbackCallResult(
            provider=local_result.provider,
            model=local_result.model,
            text=local_result.text,
            error_code=local_result.error_code,
            error_text=local_result.error_text,
            route="local_verify_fallback",
            external_call_delta=0,
        )

    verify_call = llm_client.generate(
        call_type=RAG_CALL_VERIFY,
        instruction=_verification_instruction(),
        payload=_verification_payload(
            question=question,
            answer_draft=answer_draft,
            retrieval=retrieval,
            decision_lines=decision_lines,
        ),
        ask_request_id=ask_request_id,
    )
    return VerifyFallbackCallResult(
        provider=verify_call.provider,
        model=verify_call.model,
        text=verify_call.text,
        error_code=verify_call.error_code,
        error_text=verify_call.error_text,
        route="external_verify_fallback",
        external_call_delta=1 if _is_external_provider_name(verify_call.provider) else 0,
    )


def _claim_assessments_from_verification(
    *,
    answer_claims: list[_AnswerClaim],
    verification_semantic_claims: list[dict[str, Any]],
    decision_line_by_id: dict[str, _DecisionLine],
    semantic_source: str,
) -> list[_ClaimAssessment]:
    if not verification_semantic_claims:
        return []

    if len(verification_semantic_claims) != len(answer_claims):
        return []

    assessments: list[_ClaimAssessment] = []
    for idx, answer_claim in enumerate(answer_claims):
        row = verification_semantic_claims[idx]
        score = _as_float_0_1(row.get("semantic_similarity_score"))
        decision_line_id = _as_optional_str(row.get("best_decision_line_id"))
        if score is None or not decision_line_id:
            return []

        decision_line = decision_line_by_id.get(decision_line_id)
        if decision_line is None:
            return []

        citation_chunk_ids = _normalize_chunk_ids(row.get("citation_chunk_ids") or answer_claim.citation_chunk_ids)
        if not citation_chunk_ids:
            citation_chunk_ids = list(answer_claim.citation_chunk_ids)

        claim_text = _as_optional_str(row.get("text")) or answer_claim.text
        semantic_rationale = _as_optional_str(row.get("semantic_rationale"))
        topic_hits = sorted(set(_hebrew_tokens(decision_line.text)).intersection(_primary_topic_tokens(claim_text)))
        assessments.append(
            _ClaimAssessment(
                text=claim_text,
                citation_chunk_ids=citation_chunk_ids,
                score=score,
                score_band=_claim_score_band(score),
                matched_decision_line_id=decision_line_id,
                matched_decision_text=decision_line.text,
                semantic_similarity_score=score,
                semantic_rationale=semantic_rationale,
                semantic_source=semantic_source,
                topic_hits=topic_hits,
                support_hits=[],
                secondary_context_hits=[],
                claim_topic_coverage=score,
                evidence_topic_coverage=score,
            )
        )

    return assessments


def _primary_topic_tokens(question: str) -> set[str]:
    normalized_question = normalize_for_search(question)
    if not normalized_question:
        return set()

    first_clause = TOPIC_CLAUSE_SPLIT_RE.split(normalized_question, maxsplit=1)[0]
    tokens = [token for token in HEBREW_TOKEN_RE.findall(first_clause) if len(token) >= 3]
    return {token for token in tokens if token not in GENERIC_QUERY_TOKENS}


def _should_require_decision_claims(question: str) -> bool:
    topic_tokens = _primary_topic_tokens(question)
    # Require explicit decision outcomes primarily for short/generic prompts
    # (e.g., "פינויים") where context-only claims are common.
    return len(topic_tokens) <= 1


def _assess_claims(
    *,
    question: str,
    claims: list[_AnswerClaim],
    context_by_chunk: dict[str, RagContextChunk],
    decision_lines_by_chunk: dict[str, list[_DecisionLine]],
) -> list[_ClaimAssessment]:
    topic_tokens = sorted(_primary_topic_tokens(question))
    question_tokens = set(_hebrew_tokens(normalize_for_search(question)))

    assessments: list[_ClaimAssessment] = []
    for claim in claims:
        claim_norm = normalize_for_search(claim.text)
        claim_tokens = set(_hebrew_tokens(claim_norm))

        candidate_lines: list[_DecisionLine] = []
        for chunk_id in claim.citation_chunk_ids:
            candidate_lines.extend(decision_lines_by_chunk.get(chunk_id, []))

        best_line: _DecisionLine | None = None
        best_line_tokens: set[str] = set()
        best_score = 0.0
        best_claim_overlap = 0.0
        best_question_overlap = 0.0
        best_support_hits: list[str] = []

        for line in candidate_lines:
            line_norm = normalize_for_search(line.text)
            line_tokens = set(_hebrew_tokens(line_norm))
            if not line_tokens:
                continue

            claim_overlap = _token_overlap_ratio(claim_tokens, line_tokens)
            if topic_tokens:
                question_overlap = _token_overlap_ratio(set(topic_tokens), line_tokens)
            elif question_tokens:
                question_overlap = _token_overlap_ratio(question_tokens, line_tokens)
            else:
                question_overlap = claim_overlap

            support_hits = sorted(token for token in TOPIC_SUPPORT_HINT_TOKENS if token in claim_tokens or token in line_tokens)
            support_bonus = SUPPORT_HINT_BONUS if support_hits else 0.0
            phrase_bonus = 0.1 if _contains_exact_phrase(claim_norm, line_norm) else 0.0
            negation_penalty = 0.35 if _has_negation_conflict(claim_norm, line_norm) else 0.0

            secondary_context_hits = sorted(token for token in SECONDARY_CONTEXT_HINT_TOKENS if token in claim_tokens)
            cross_domain_penalty = 0.0
            if secondary_context_hits and {"בטיחות", "בדרכים"}.intersection(topic_tokens):
                cross_domain_penalty = CROSS_DOMAIN_PENALTY

            score = _round_score(
                _clamp_score(
                    (CLAIM_TOPIC_COVERAGE_WEIGHT * claim_overlap)
                    + (EVIDENCE_TOPIC_COVERAGE_WEIGHT * question_overlap)
                    + support_bonus
                    + phrase_bonus
                    - cross_domain_penalty
                    - negation_penalty
                )
            )
            if best_line is None or score > best_score:
                best_line = line
                best_line_tokens = line_tokens
                best_score = score
                best_claim_overlap = _round_score(claim_overlap)
                best_question_overlap = _round_score(question_overlap)
                best_support_hits = support_hits

        secondary_context_hits = sorted(token for token in SECONDARY_CONTEXT_HINT_TOKENS if token in claim_tokens)
        matched_decision_line_id = best_line.decision_line_id if best_line else None
        matched_decision_text = best_line.text if best_line else None
        topic_hits = sorted(token for token in topic_tokens if token in claim_tokens or token in best_line_tokens)
        semantic_rationale = (
            f"deterministic overlap: claim_line={best_claim_overlap}, question_line={best_question_overlap}"
            if best_line
            else "deterministic overlap: missing usable decision line"
        )
        assessments.append(
            _ClaimAssessment(
                text=claim.text,
                citation_chunk_ids=list(claim.citation_chunk_ids),
                score=best_score,
                score_band=_claim_score_band(best_score),
                matched_decision_line_id=matched_decision_line_id,
                matched_decision_text=matched_decision_text,
                semantic_similarity_score=best_score,
                semantic_rationale=semantic_rationale,
                semantic_source=DETERMINISTIC_SIMILARITY_SOURCE,
                topic_hits=topic_hits,
                support_hits=best_support_hits,
                secondary_context_hits=secondary_context_hits,
                claim_topic_coverage=best_claim_overlap,
                evidence_topic_coverage=best_question_overlap,
            )
        )

    return assessments


def _select_claims_for_answer(claim_assessments: list[_ClaimAssessment]) -> list[_ClaimAssessment]:
    normalized_claims = [normalize_for_search(item.text) for item in claim_assessments]
    has_decision_claims = any(_has_decision_marker(value) for value in normalized_claims)

    selected: list[_ClaimAssessment] = []
    for item, claim_norm in zip(claim_assessments, normalized_claims, strict=False):
        item.selected_for_answer = True
        item.exclusion_reason = None

        has_decision_marker = _has_decision_marker(claim_norm)

        if has_decision_claims and not has_decision_marker:
            item.selected_for_answer = False
            item.exclusion_reason = "non_decision_claim_when_decision_claims_present"

        if item.selected_for_answer:
            selected.append(item)

    return selected


def _chunk_ids_from_claim_assessments(claim_assessments: list[_ClaimAssessment]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in claim_assessments:
        for chunk_id in item.citation_chunk_ids:
            if chunk_id in seen:
                continue
            ordered.append(chunk_id)
            seen.add(chunk_id)
    return ordered


def _chunk_ids_from_decision_lines(lines: list[_DecisionEvidenceLine]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for row in lines:
        if row.chunk_id in seen:
            continue
        ordered.append(row.chunk_id)
        seen.add(row.chunk_id)
    return ordered


def _merge_chunk_ids(primary: list[str], extra: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for chunk_id in [*primary, *extra]:
        if chunk_id in seen:
            continue
        ordered.append(chunk_id)
        seen.add(chunk_id)
    return ordered


def _section_chunk_ids(*sections_groups: list[dict[str, Any]]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for sections in sections_groups:
        for section in sections:
            if not isinstance(section, dict):
                continue
            chunk_ids = section.get("chunk_ids")
            if not isinstance(chunk_ids, list):
                continue
            for chunk_id in chunk_ids:
                key = str(chunk_id)
                if not key or key in seen:
                    continue
                seen.add(key)
                ordered.append(key)
    return ordered


def _protocol_document_ids_from_contexts(contexts: list[RagContextChunk]) -> list[int]:
    ordered: list[int] = []
    seen: set[int] = set()
    for context in contexts:
        if context.source_kind != "protocol":
            continue
        if context.document_id in seen:
            continue
        ordered.append(context.document_id)
        seen.add(context.document_id)
    return ordered


def _protocol_document_ids_from_chunk_ids(
    chunk_ids: list[str],
    context_by_chunk: dict[str, RagContextChunk],
) -> list[int]:
    ordered: list[int] = []
    seen: set[int] = set()
    for chunk_id in chunk_ids:
        context = context_by_chunk.get(chunk_id)
        if context is None or context.source_kind != "protocol":
            continue
        if context.document_id in seen:
            continue
        ordered.append(context.document_id)
        seen.add(context.document_id)
    return ordered


def _protocol_document_ids_from_decision_lines(
    lines: list[_DecisionEvidenceLine],
    context_by_chunk: dict[str, RagContextChunk],
) -> list[int]:
    return _protocol_document_ids_from_chunk_ids(
        [row.chunk_id for row in lines],
        context_by_chunk,
    )


def _should_enforce_protocol_coverage(*, question: str, retrieval_contexts: list[RagContextChunk]) -> bool:
    if _primary_topic_tokens(question):
        return False
    protocol_doc_ids = _protocol_document_ids_from_contexts(retrieval_contexts)
    return len(protocol_doc_ids) > 1


def _missing_protocol_coverage_lines(
    *,
    question: str,
    retrieval_contexts: list[RagContextChunk],
    selected_chunk_ids: list[str],
    context_by_chunk: dict[str, RagContextChunk],
    decision_lines_by_chunk: dict[str, list[_DecisionLine]],
) -> list[_DecisionEvidenceLine]:
    missing_doc_ids = [
        doc_id
        for doc_id in _protocol_document_ids_from_contexts(retrieval_contexts)
        if doc_id not in set(_protocol_document_ids_from_chunk_ids(selected_chunk_ids, context_by_chunk))
    ]
    if not missing_doc_ids:
        return []

    contexts_by_doc_id: dict[int, list[RagContextChunk]] = {}
    for context in retrieval_contexts:
        if context.source_kind != "protocol":
            continue
        contexts_by_doc_id.setdefault(context.document_id, []).append(context)

    selected_lines: list[_DecisionEvidenceLine] = []
    seen_line_texts: set[str] = set()
    for doc_id in missing_doc_ids:
        candidates: list[tuple[float, _DecisionEvidenceLine]] = []
        for context in contexts_by_doc_id.get(doc_id, []):
            source_rows = decision_lines_by_chunk.get(context.chunk_id, [])
            line_texts = [row.text for row in source_rows]
            if not line_texts:
                source_text = (context.chunk_text or context.snippet or "").strip()
                line_texts = _extract_decision_sentences(source_text)

            for text in line_texts:
                compact_text = _compact_summary_text(text)
                if not compact_text:
                    continue
                normalized = normalize_for_search(compact_text)
                if not normalized or normalized in seen_line_texts:
                    continue
                score = _decision_candidate_score(question=question, candidate_text=compact_text)
                score += _round_score(max(0.0, min(1.0, context.score)) * 0.05)
                candidates.append((score, _DecisionEvidenceLine(text=compact_text, chunk_id=context.chunk_id)))

        if not candidates:
            continue
        candidates.sort(key=lambda row: row[0], reverse=True)
        best = candidates[0][1]
        selected_lines.append(best)
        seen_line_texts.add(normalize_for_search(best.text))

    return selected_lines


def _compose_answer_from_claim_assessments(claim_assessments: list[_ClaimAssessment]) -> str:
    lines: list[str] = []
    for item in claim_assessments:
        text = item.text.strip()
        if not text:
            continue
        if text[-1] not in {".", "?", "!", ";"}:
            text = f"{text}."
        lines.append(text)
    return " ".join(lines).strip()


def _decision_summary_items_for_answer(
    *,
    question: str,
    answer_draft: _AnswerDraft,
    selected_claim_assessments: list[_ClaimAssessment],
    reconstructed_decision_lines: list[_DecisionEvidenceLine],
    decision_reconstruction_used: bool,
    protocol_coverage_lines: list[_DecisionEvidenceLine],
    context_by_chunk: dict[str, RagContextChunk],
) -> list[dict[str, Any]]:
    default_topic = _default_topic_name(question)
    decision_item_topics = _decision_item_topics(
        decision_items=answer_draft.decision_items,
        context_by_chunk=context_by_chunk,
    )
    topic_hints_by_chunk = _decision_item_topic_hints_by_chunk(
        decision_items=answer_draft.decision_items,
        context_by_chunk=context_by_chunk,
    )

    items: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, ...], str]] = set()

    def append_item(
        *,
        summary_text: str,
        chunk_ids: list[str],
        extended_text: str | None = None,
        explicit_topic_name: str | None = None,
    ) -> None:
        summary = _compact_summary_text(_as_optional_str(summary_text) or "")
        if not summary:
            return
        extended_summary = _extended_summary_text(_as_optional_str(extended_text) or summary)
        if not extended_summary:
            extended_summary = summary

        normalized_chunk_ids = [chunk_id for chunk_id in chunk_ids if chunk_id in context_by_chunk]
        if not normalized_chunk_ids:
            return
        key = (tuple(normalized_chunk_ids), normalize_for_search(summary))
        if key in seen:
            return
        seen.add(key)

        first_chunk_id = normalized_chunk_ids[0]
        topic_hint_payload = _match_topic_hint_for_summary(
            summary_text=summary,
            chunk_ids=normalized_chunk_ids,
            decision_item_topics=decision_item_topics,
        ) or topic_hints_by_chunk.get(first_chunk_id, {})
        topic_hint = _as_optional_str(explicit_topic_name) or _as_optional_str(topic_hint_payload.get("topic_name_he"))
        topic_name = topic_hint or _fallback_topic_for_chunk(
            chunk_id=first_chunk_id,
            context_by_chunk=context_by_chunk,
            default_topic=default_topic,
        )
        items.append(
            {
                "summary_he": summary,
                "extended_summary_he": extended_summary,
                "topic_name_he": topic_name,
                "topic_hint_he": topic_hint,
                "topic_root_hint_he": _as_optional_str(topic_hint_payload.get("topic_root_he")),
                "topic_subtopic_hint_he": _as_optional_str(topic_hint_payload.get("topic_subtopic_he")),
                "topic_confidence_hint": _as_float_0_1(topic_hint_payload.get("topic_confidence")),
                "topic_granularity_hint": _as_int_in_range(
                    topic_hint_payload.get("topic_granularity_level"),
                    min_value=1,
                    max_value=10,
                ),
                "citation_chunk_ids": normalized_chunk_ids,
            }
        )

    if decision_reconstruction_used and reconstructed_decision_lines:
        for row in reconstructed_decision_lines:
            append_item(
                summary_text=row.text,
                chunk_ids=[row.chunk_id],
                extended_text=row.text,
            )
    else:
        for item in selected_claim_assessments:
            if not item.selected_for_answer:
                continue
            append_item(
                summary_text=item.text,
                chunk_ids=list(item.citation_chunk_ids),
                extended_text=item.matched_decision_text or item.text,
            )

    for row in protocol_coverage_lines:
        append_item(
            summary_text=row.text,
            chunk_ids=[row.chunk_id],
            extended_text=row.text,
        )

    if items:
        return items

    fallback: list[dict[str, Any]] = []
    for item in answer_draft.decision_items:
        chunk_ids = item.get("citation_chunk_ids") or []
        if not all(chunk_id in context_by_chunk for chunk_id in chunk_ids):
            continue
        fallback.append(
            {
                "summary_he": _compact_summary_text(_as_optional_str(item.get("summary_he")) or ""),
                "extended_summary_he": _extended_summary_text(_as_optional_str(item.get("summary_he")) or ""),
                "topic_name_he": _as_optional_str(item.get("topic_root_he"))
                or _as_optional_str(item.get("topic_name_he"))
                or default_topic,
                "topic_hint_he": _as_optional_str(item.get("topic_name_he")),
                "topic_root_hint_he": _as_optional_str(item.get("topic_root_he")),
                "topic_subtopic_hint_he": _as_optional_str(item.get("topic_subtopic_he")),
                "topic_confidence_hint": _as_float_0_1(item.get("topic_confidence")),
                "topic_granularity_hint": _as_int_in_range(
                    item.get("topic_granularity_level"),
                    min_value=1,
                    max_value=10,
                ),
                "citation_chunk_ids": list(chunk_ids),
            }
        )
    return fallback


def _decision_item_topic_hints_by_chunk(
    *,
    decision_items: list[dict[str, Any]],
    context_by_chunk: dict[str, RagContextChunk],
) -> dict[str, dict[str, Any]]:
    hints: dict[str, dict[str, Any]] = {}
    for item in decision_items:
        topic_root = _as_optional_str(item.get("topic_root_he"))
        topic_subtopic = _as_optional_str(item.get("topic_subtopic_he"))
        topic_name = _as_optional_str(item.get("topic_name_he")) or topic_root
        topic_confidence = _as_float_0_1(item.get("topic_confidence"))
        if not topic_name and not topic_root and not topic_subtopic:
            continue
        chunk_ids = item.get("citation_chunk_ids")
        if not isinstance(chunk_ids, list):
            continue
        for chunk_id in chunk_ids:
            if chunk_id in context_by_chunk and chunk_id not in hints:
                hints[chunk_id] = {
                    "topic_name_he": topic_name,
                    "topic_root_he": topic_root,
                    "topic_subtopic_he": topic_subtopic,
                    "topic_confidence": topic_confidence,
                    "topic_granularity_level": _as_int_in_range(
                        item.get("topic_granularity_level"),
                        min_value=1,
                        max_value=10,
                    ),
                }
    return hints


def _decision_item_topics(
    *,
    decision_items: list[dict[str, Any]],
    context_by_chunk: dict[str, RagContextChunk],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in decision_items:
        chunk_ids = item.get("citation_chunk_ids")
        if not isinstance(chunk_ids, list):
            continue
        normalized_chunk_ids = [chunk_id for chunk_id in chunk_ids if chunk_id in context_by_chunk]
        if not normalized_chunk_ids:
            continue
        summary = _as_optional_str(item.get("summary_he"))
        if not summary:
            continue

        out.append(
            {
                "summary_he": summary,
                "topic_name_he": _as_optional_str(item.get("topic_name_he")),
                "topic_root_he": _as_optional_str(item.get("topic_root_he")),
                "topic_subtopic_he": _as_optional_str(item.get("topic_subtopic_he")),
                "topic_confidence": _as_float_0_1(item.get("topic_confidence")),
                "topic_granularity_level": _as_int_in_range(
                    item.get("topic_granularity_level"),
                    min_value=1,
                    max_value=10,
                ),
                "citation_chunk_ids": normalized_chunk_ids,
            }
        )
    return out


def _match_topic_hint_for_summary(
    *,
    summary_text: str,
    chunk_ids: list[str],
    decision_item_topics: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not decision_item_topics:
        return None

    summary_tokens = set(_normalized_topic_tokens(summary_text))
    if not summary_tokens:
        return None

    chunk_id_set = set(chunk_ids)
    best_payload: dict[str, Any] | None = None
    best_score = 0.0
    for item in decision_item_topics:
        candidate_chunk_ids = item.get("citation_chunk_ids")
        if not isinstance(candidate_chunk_ids, list):
            continue
        if not chunk_id_set.intersection(candidate_chunk_ids):
            continue

        candidate_summary = _as_optional_str(item.get("summary_he"))
        if not candidate_summary:
            continue
        candidate_tokens = set(_normalized_topic_tokens(candidate_summary))
        if not candidate_tokens:
            continue

        score = _token_overlap_ratio(summary_tokens, candidate_tokens)
        if candidate_chunk_ids and chunk_ids and candidate_chunk_ids[0] == chunk_ids[0]:
            score += 0.08
        if _as_optional_str(item.get("topic_subtopic_he")):
            score += 0.04

        if score > best_score:
            best_score = score
            best_payload = {
                "topic_name_he": _as_optional_str(item.get("topic_name_he")),
                "topic_root_he": _as_optional_str(item.get("topic_root_he")),
                "topic_subtopic_he": _as_optional_str(item.get("topic_subtopic_he")),
                "topic_confidence": _as_float_0_1(item.get("topic_confidence")),
                "topic_granularity_level": _as_int_in_range(
                    item.get("topic_granularity_level"),
                    min_value=1,
                    max_value=10,
                ),
            }

    return best_payload


def _fallback_topic_for_chunk(
    *,
    chunk_id: str,
    context_by_chunk: dict[str, RagContextChunk],
    default_topic: str,
) -> str:
    context = context_by_chunk.get(chunk_id)
    if context is None:
        return default_topic

    title_tokens = [
        token
        for token in _hebrew_tokens(normalize_for_search(context.document_title))
        if token not in GENERIC_QUERY_TOKENS and token not in {"ועדת", "ועדה", "פרוטוקול", "מספר"}
    ]
    if not title_tokens:
        return default_topic
    return " ".join(title_tokens[:4])


def _compact_summary_text(value: str, *, max_sentences: int = 2, max_chars: int = 320) -> str:
    compact = " ".join(value.split())
    if not compact:
        return ""

    sentences = [part.strip() for part in re.split(r"(?<=[\.!?;])\s+", compact) if part.strip()]
    if len(sentences) > max_sentences:
        compact = " ".join(sentences[:max_sentences]).strip()

    if len(compact) <= max_chars:
        return compact
    clipped = compact[:max_chars].rsplit(" ", 1)[0].strip()
    if not clipped:
        clipped = compact[:max_chars].strip()
    return f"{clipped}..."


def _extended_summary_text(value: str, *, max_sentences: int = 4, max_chars: int = 720) -> str:
    compact = " ".join(value.split())
    if not compact:
        return ""

    sentences = [part.strip() for part in re.split(r"(?<=[\.!?;])\s+", compact) if part.strip()]
    if len(sentences) > max_sentences:
        compact = " ".join(sentences[:max_sentences]).strip()

    if len(compact) <= max_chars:
        return compact
    clipped = compact[:max_chars].rsplit(" ", 1)[0].strip()
    if not clipped:
        clipped = compact[:max_chars].strip()
    return f"{clipped}..."


def _build_answer_sections(
    *,
    summary_items: list[dict[str, Any]],
    context_by_chunk: dict[str, RagContextChunk],
    fallback_topic: str,
    cached_topic_tree: dict[str, list[str]] | None = None,
    protocol_subject_anchors: dict[int, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    concise_sections: list[dict[str, Any]] = []
    extended_sections: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, ...], str]] = set()
    protocol_topic_trees = _build_protocol_topic_trees(
        summary_items=summary_items,
        context_by_chunk=context_by_chunk,
        fallback_topic=fallback_topic,
        cached_topic_tree=cached_topic_tree,
    )

    for item in summary_items:
        summary = _as_optional_str(item.get("summary_he"))
        extended_summary = _as_optional_str(item.get("extended_summary_he")) or summary
        chunk_ids = item.get("citation_chunk_ids")
        if not summary or not isinstance(chunk_ids, list) or not chunk_ids:
            continue

        normalized_chunk_ids = [chunk_id for chunk_id in chunk_ids if chunk_id in context_by_chunk]
        if not normalized_chunk_ids:
            continue

        dedupe_key = (tuple(normalized_chunk_ids), normalize_for_search(summary))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        first_chunk = context_by_chunk.get(normalized_chunk_ids[0])
        protocol_title = first_chunk.document_title if first_chunk else "פרוטוקול ללא כותרת"
        protocol_tree = protocol_topic_trees.get(protocol_title) or {
            "root_topic": _protocol_root_topic(protocol_title=protocol_title, fallback_topic=fallback_topic),
            "children": [],
        }
        topic_name, topic_route, topic_score = _resolve_topic_for_summary_item(
            item=item,
            protocol_tree=protocol_tree,
            fallback_topic=fallback_topic,
            context_by_chunk=context_by_chunk,
            document_id=first_chunk.document_id if first_chunk else None,
            protocol_subject_anchors=protocol_subject_anchors,
        )

        concise_sections.append(
            {
                "protocol_title": protocol_title,
                "topic_name": topic_name,
                "text": summary,
                "chunk_ids": list(normalized_chunk_ids),
                "topic_route": topic_route,
                "topic_score": topic_score,
                "topic_granularity_level": _as_int_in_range(item.get("topic_granularity_hint"), min_value=1, max_value=10),
            }
        )
        extended_sections.append(
            {
                "protocol_title": protocol_title,
                "topic_name": topic_name,
                "text": extended_summary or summary,
                "chunk_ids": list(normalized_chunk_ids),
                "topic_route": topic_route,
                "topic_score": topic_score,
                "topic_granularity_level": _as_int_in_range(item.get("topic_granularity_hint"), min_value=1, max_value=10),
            }
        )

    return concise_sections, extended_sections


def _expand_sections_by_protocol_for_broad_query(
    *,
    question: str,
    answer_sections: list[dict[str, Any]],
    extended_answer_sections: list[dict[str, Any]],
    context_by_chunk: dict[str, RagContextChunk],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    if not answer_sections:
        return answer_sections, extended_answer_sections, False
    if _primary_topic_tokens(question):
        return answer_sections, extended_answer_sections, False

    expanded_concise: list[dict[str, Any]] = []
    expanded_extended: list[dict[str, Any]] = []
    seen_protocol_titles: set[str] = set()
    changed = False

    for index, section in enumerate(answer_sections):
        if not isinstance(section, dict):
            continue

        paired_extended = (
            extended_answer_sections[index]
            if index < len(extended_answer_sections) and isinstance(extended_answer_sections[index], dict)
            else None
        )

        section_title = str(section.get("protocol_title") or "").strip() or "פרוטוקול"
        chunk_ids_raw = section.get("chunk_ids")
        chunk_ids: list[str] = []
        if isinstance(chunk_ids_raw, list):
            for chunk_id in chunk_ids_raw:
                normalized = str(chunk_id).strip()
                if normalized and normalized not in chunk_ids:
                    chunk_ids.append(normalized)

        grouped_chunk_ids: dict[str, list[str]] = {}
        protocol_order: list[str] = []
        for chunk_id in chunk_ids:
            context = context_by_chunk.get(chunk_id)
            protocol_title = (
                str(context.document_title).strip()
                if context is not None and str(context.document_title).strip()
                else section_title
            )
            if protocol_title not in grouped_chunk_ids:
                grouped_chunk_ids[protocol_title] = []
                protocol_order.append(protocol_title)
            grouped_chunk_ids[protocol_title].append(chunk_id)

        if not grouped_chunk_ids:
            grouped_chunk_ids = {section_title: chunk_ids}
            protocol_order = [section_title]

        if len(protocol_order) > 1:
            changed = True

        for protocol_title in protocol_order:
            if protocol_title in seen_protocol_titles:
                changed = True
                continue
            seen_protocol_titles.add(protocol_title)

            section_copy = dict(section)
            section_copy["protocol_title"] = protocol_title
            section_copy["chunk_ids"] = list(grouped_chunk_ids.get(protocol_title, []))
            expanded_concise.append(section_copy)

            if paired_extended is None:
                paired_copy = dict(section_copy)
            else:
                paired_copy = dict(paired_extended)
                paired_copy["protocol_title"] = protocol_title
                paired_copy["chunk_ids"] = list(grouped_chunk_ids.get(protocol_title, []))
            expanded_extended.append(paired_copy)

    if len(expanded_extended) != len(expanded_concise):
        expanded_extended = [
            dict(section)
            for section in expanded_concise
        ]
        changed = True

    if not expanded_concise:
        return answer_sections, extended_answer_sections, changed
    if len(expanded_concise) != len(answer_sections):
        changed = True
    return expanded_concise, expanded_extended, changed


def _enforce_semantic_topics_and_section_uniqueness(
    *,
    question: str,
    answer_sections: list[dict[str, Any]],
    extended_answer_sections: list[dict[str, Any]],
    context_by_chunk: dict[str, RagContextChunk],
    protocol_semantic_topic_labels: dict[int, list[str]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, bool]:
    if not answer_sections:
        return answer_sections, extended_answer_sections, False, False
    if protocol_semantic_topic_labels is None:
        return answer_sections, extended_answer_sections, False, False

    broad_query = not _primary_topic_tokens(question)
    semantic_changed = False
    duplicate_fixed = False

    concise_sections: list[dict[str, Any]] = []
    extended_sections: list[dict[str, Any]] = []

    for index, section in enumerate(answer_sections):
        if not isinstance(section, dict):
            continue
        section_copy = dict(section)
        extended_copy = (
            dict(extended_answer_sections[index])
            if index < len(extended_answer_sections) and isinstance(extended_answer_sections[index], dict)
            else dict(section_copy)
        )

        semantic_topic = _section_semantic_topic_label(
            section=section_copy,
            context_by_chunk=context_by_chunk,
            protocol_semantic_topic_labels=protocol_semantic_topic_labels,
        )
        resolved_topic = _resolve_section_topic_name(
            section=section_copy,
            semantic_topic=semantic_topic,
            context_by_chunk=context_by_chunk,
        )
        if str(section_copy.get("topic_name") or "").strip() != resolved_topic:
            semantic_changed = True

        section_copy["topic_name"] = resolved_topic
        section_copy["topic_route"] = "semantic_label" if semantic_topic else "object_topic_inferred"
        section_copy["topic_score"] = 1.0 if semantic_topic else 0.65

        compact_text = _sanitize_summary_for_output(_as_optional_str(section_copy.get("text")) or "")
        if _is_subjectless_publication_summary(compact_text):
            compact_text = _enrich_subjectless_summary(compact_text, resolved_topic)

        allocation_enriched_text, detail_chunk_ids = _enrich_allocation_summary_with_context(
            section={
                **section_copy,
                "text": compact_text,
                "topic_name": resolved_topic,
            },
            context_by_chunk=context_by_chunk,
        )
        if allocation_enriched_text:
            compact_text = allocation_enriched_text
            merged_chunk_ids = _merge_section_chunk_ids(section_copy.get("chunk_ids"), detail_chunk_ids)
            if merged_chunk_ids:
                section_copy["chunk_ids"] = merged_chunk_ids
                extended_copy["chunk_ids"] = merged_chunk_ids

        if compact_text:
            section_copy["text"] = compact_text

        extended_copy["topic_name"] = resolved_topic
        extended_copy["topic_route"] = section_copy["topic_route"]
        extended_copy["topic_score"] = section_copy["topic_score"]
        extended_text = _sanitize_summary_for_output(_as_optional_str(extended_copy.get("text")) or "")
        if _is_subjectless_publication_summary(extended_text):
            extended_text = _enrich_subjectless_summary(extended_text, resolved_topic)
        if allocation_enriched_text:
            extended_text = allocation_enriched_text
        if extended_text:
            extended_copy["text"] = extended_text

        concise_sections.append(section_copy)
        extended_sections.append(extended_copy)

    if broad_query:
        seen_texts: set[str] = set()
        for index, section in enumerate(concise_sections):
            text_value = _compact_summary_text(_as_optional_str(section.get("text")) or "")
            normalized_text = normalize_for_search(text_value)
            if not normalized_text:
                continue

            if _is_boilerplate_summary_text(normalized_text) or _is_low_quality_summary_text(normalized_text):
                replacement = _best_alternative_section_text(
                    section=section,
                    context_by_chunk=context_by_chunk,
                    blocked_texts=seen_texts,
                )
                if replacement:
                    if _is_subjectless_publication_summary(replacement):
                        replacement = _enrich_subjectless_summary(
                            replacement,
                            _as_optional_str(section.get("topic_name")) or "",
                        )
                    concise_sections[index]["text"] = replacement
                    extended_sections[index]["text"] = replacement
                    normalized_text = normalize_for_search(replacement)
                    duplicate_fixed = True

            if normalized_text not in seen_texts:
                seen_texts.add(normalized_text)
                continue

            replacement = _best_alternative_section_text(
                section=section,
                context_by_chunk=context_by_chunk,
                blocked_texts=seen_texts,
            )
            if not replacement:
                qualifier = _protocol_title_subject_hint(_as_optional_str(section.get("protocol_title")) or "")
                if qualifier:
                    replacement = f"{text_value.rstrip('.')} בהקשר {qualifier}."
                else:
                    continue
            if _is_subjectless_publication_summary(replacement):
                replacement = _enrich_subjectless_summary(
                    replacement,
                    _as_optional_str(section.get("topic_name")) or "",
                )

            concise_sections[index]["text"] = replacement
            extended_text_value = _as_optional_str(extended_sections[index].get("text")) or ""
            if not extended_text_value or normalize_for_search(extended_text_value) == normalized_text:
                extended_sections[index]["text"] = replacement

            seen_texts.add(normalize_for_search(replacement))
            duplicate_fixed = True

    return concise_sections, extended_sections, semantic_changed, duplicate_fixed


def _section_semantic_topic_label(
    *,
    section: dict[str, Any],
    context_by_chunk: dict[str, RagContextChunk],
    protocol_semantic_topic_labels: dict[int, list[str]],
) -> str | None:
    chunk_ids_raw = section.get("chunk_ids")
    if not isinstance(chunk_ids_raw, list):
        return None

    candidates: list[str] = []
    seen: set[str] = set()
    document_ids: list[int] = []
    section_text = _as_optional_str(section.get("text")) or ""
    protocol_title = _as_optional_str(section.get("protocol_title")) or ""

    for chunk_id in chunk_ids_raw:
        context = context_by_chunk.get(str(chunk_id))
        if context is None:
            continue
        if context.document_id not in document_ids:
            document_ids.append(context.document_id)
        for label in context.semantic_topic_labels:
            candidate = _sanitize_semantic_topic_label(label)
            if not candidate:
                continue
            key = normalize_for_search(candidate)
            if not key or key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)

    for document_id in document_ids:
        for label in protocol_semantic_topic_labels.get(int(document_id), []):
            candidate = _sanitize_semantic_topic_label(label)
            if not candidate:
                continue
            key = normalize_for_search(candidate)
            if not key or key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)

    if not candidates:
        return None

    ranked = sorted(
        candidates,
        key=lambda candidate: _semantic_topic_candidate_score(
            candidate=candidate,
            section_text=section_text,
            protocol_title=protocol_title,
        ),
        reverse=True,
    )
    if not ranked:
        return None
    best = ranked[0]
    best_score = _semantic_topic_candidate_score(candidate=best, section_text=section_text, protocol_title=protocol_title)
    second_score = (
        _semantic_topic_candidate_score(candidate=ranked[1], section_text=section_text, protocol_title=protocol_title)
        if len(ranked) > 1
        else 0.0
    )
    if best_score < 0.17:
        return None
    if second_score > 0 and (best_score - second_score) < 0.035:
        return None
    return best


def _topic_path_parts(topic_name: str | None) -> tuple[str, str | None]:
    raw = " ".join(str(topic_name or "").split())
    if not raw:
        return "", None
    parts = [part.strip() for part in raw.split(">") if part and part.strip()]
    if len(parts) >= 2:
        return parts[0], " > ".join(parts[1:])
    return raw, None


def _topic_display_label(topic_name: str | None) -> str:
    root, child = _topic_path_parts(topic_name)
    return child or root


def _object_root_from_texts(*texts: str) -> str | None:
    token_set: set[str] = set()
    for text in texts:
        token_set.update(_normalized_topic_tokens(text))
    if not token_set:
        return None

    best_root: str | None = None
    best_score = 0
    for root_name, keywords in OBJECT_ROOT_KEYWORDS.items():
        score = 0
        for token in token_set:
            if any(_topic_tokens_match(token, keyword) for keyword in keywords):
                score += 1
        if score > best_score:
            best_score = score
            best_root = root_name
    if best_root is None or best_score <= 0:
        return None
    return best_root


def _agreement_child_from_texts(*texts: str, prefer_association: bool = False) -> str | None:
    combined = normalize_for_search(" ".join(texts))
    if "הסכם" not in combined:
        return None

    entity_suffix = ""
    if any(token in combined for token in {"לעמותה", "עמותה", "עמותת", "עמותות"}):
        entity_suffix = " לעמותה"
    elif prefer_association:
        entity_suffix = " לעמותה"
    elif any(token in combined for token in {"לעירייה", "העירייה", "עירייה"}):
        entity_suffix = " לעירייה"

    if "רשות" in combined:
        return f"הסכם רשות{entity_suffix}".strip()
    return f"הסכם{entity_suffix}".strip()


def _allocation_child_from_texts(*texts: str) -> str | None:
    combined = normalize_for_search(" ".join(texts))
    if "הקצא" not in combined and "עמות" not in combined:
        return None

    if "ביטול" in combined and "הקצא" in combined:
        return "ביטול הקצאה"
    if "כיתת" in combined and ("ילדים" in combined or "גן" in combined):
        return "הקצאת כיתת ילדים"
    if "רווחה" in combined and "מבנ" in combined:
        return "הקצאה למבני רווחה"
    if "קרקע" in combined and "מבנ" in combined:
        return "הקצאת קרקעות ומבנים"
    if "עמות" in combined:
        return "הקצאה לעמותה"
    if "פרסום" in combined and "בעיתונות" in combined:
        return "אישור הקצאה"
    if "החלטת" in combined and "הקצא" in combined:
        return "אישור הקצאה"
    return "אישור הקצאה"


def _is_generic_allocation_child(value: str | None) -> bool:
    normalized = normalize_for_search(value or "")
    if not normalized:
        return True
    return normalized in {
        normalize_for_search("בקשה להקצאה"),
        normalize_for_search("החלטת הקצאות"),
        normalize_for_search("פרסום בעיתונות"),
        normalize_for_search("החלטה ענפית"),
        normalize_for_search("החלטה כללית"),
    }


def _resolve_section_topic_name(
    *,
    section: dict[str, Any],
    semantic_topic: str | None,
    context_by_chunk: dict[str, RagContextChunk],
) -> str:
    existing_topic_raw = _as_optional_str(section.get("topic_name")) or ""
    protocol_title = _as_optional_str(section.get("protocol_title")) or ""
    existing_root, existing_child = _topic_path_parts(existing_topic_raw)
    section_text = _as_optional_str(section.get("text")) or ""

    chunk_texts: list[str] = []
    chunk_ids = section.get("chunk_ids")
    if isinstance(chunk_ids, list):
        for chunk_id in chunk_ids:
            context = context_by_chunk.get(str(chunk_id))
            if context is None:
                continue
            text_blob = (context.chunk_text or context.snippet or "").strip()
            if text_blob:
                chunk_texts.append(text_blob)

    object_root = _object_root_from_texts(
        semantic_topic or "",
        existing_topic_raw,
        section_text,
        " ".join(chunk_texts),
    )
    if not object_root:
        object_root = _object_root_from_texts(existing_root)
    if not object_root:
        object_root = "החלטות עירוניות"

    semantic_child = _clean_topic_candidate(semantic_topic or "", max_tokens=5, min_tokens=2, drop_noise_tokens=True)
    if semantic_child and _is_subjectless_publication_summary(section_text):
        forced_root = _object_root_from_texts(semantic_child, section_text) or object_root
        return f"{forced_root} > {semantic_child}"

    prefer_association = "הקצא" in normalize_for_search(protocol_title)
    if object_root == "הסכמים" and semantic_child is None:
        forced_child = _agreement_child_from_texts(
            section_text,
            " ".join(chunk_texts),
            semantic_topic or "",
            prefer_association=prefer_association,
        )
        if forced_child:
            return f"{object_root} > {forced_child}"
    if object_root == "הקצאות" and (semantic_child is None or _is_generic_allocation_child(semantic_child)):
        forced_child = _allocation_child_from_texts(
            section_text,
            " ".join(chunk_texts),
            semantic_topic or "",
            existing_topic_raw,
            protocol_title,
        )
        if forced_child:
            return f"{object_root} > {forced_child}"

    child_candidates: list[str] = []
    if semantic_topic:
        child_candidates.append(semantic_topic)
    if existing_child:
        child_candidates.append(existing_child)
    elif existing_topic_raw:
        child_candidates.append(existing_topic_raw)

    for candidate in _topic_phrase_candidates_from_text(section_text):
        child_candidates.append(candidate)
    for text_blob in chunk_texts:
        for candidate in _topic_phrase_candidates_from_text(text_blob):
            child_candidates.append(candidate)

    if object_root == "הסכמים":
        agreement_child = _agreement_child_from_texts(
            section_text,
            " ".join(chunk_texts),
            semantic_topic or "",
            prefer_association=prefer_association,
        )
        if agreement_child:
            child_candidates.insert(0, agreement_child)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in child_candidates:
        key = normalize_for_search(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    best_child: str | None = None
    best_score = -1.0
    decision_text = " ".join([section_text, *chunk_texts]).strip()
    for candidate in deduped:
        normalized_candidate = _clean_topic_candidate(candidate, max_tokens=5, min_tokens=2, drop_noise_tokens=True)
        if not normalized_candidate:
            continue
        if normalize_for_search(normalized_candidate) in {
            normalize_for_search("ללא תיוג סמנטי"),
            normalize_for_search("נושא כללי"),
            normalize_for_search("החלטה כללית"),
        }:
            continue

        score = _topic_candidate_score(candidate=normalized_candidate, decision_text=decision_text, root_topic=object_root)
        if object_root == "הסכמים" and "הסכם" in normalize_for_search(normalized_candidate):
            score += 0.18
        if semantic_topic and _topic_overlap_ratio(
            set(_normalized_topic_tokens(normalized_candidate)),
            set(_normalized_topic_tokens(semantic_topic)),
        ) >= 0.5:
            score += 0.07
        if score > best_score:
            best_score = score
            best_child = normalized_candidate

    if not best_child:
        if object_root == "הסכמים":
            best_child = _agreement_child_from_texts(
                section_text,
                " ".join(chunk_texts),
                prefer_association=("הקצא" in normalize_for_search(protocol_title)),
            ) or "הסכם רשות"
        elif object_root == "הקצאות":
            best_child = "הקצאה לעמותה"
        else:
            best_child = _clean_topic_candidate(section_text, max_tokens=4, min_tokens=2, drop_noise_tokens=True) or "החלטה ענפית"

    if object_root == "הסכמים" and best_child.startswith("הכנת הסכם"):
        best_child = best_child.replace("הכנת ", "", 1)
        best_child = _clean_topic_candidate(best_child, max_tokens=5, min_tokens=2, drop_noise_tokens=True) or "הסכם רשות"
    if object_root == "הקצאות" and _is_generic_allocation_child(best_child):
        best_child = _allocation_child_from_texts(section_text, " ".join(chunk_texts), existing_topic_raw) or best_child

    return f"{object_root} > {best_child}"


def _semantic_topic_candidate_score(*, candidate: str, section_text: str, protocol_title: str) -> float:
    candidate_tokens = _normalized_topic_tokens(candidate)
    if not candidate_tokens:
        return 0.0

    section_tokens = set(_normalized_topic_tokens(section_text))
    protocol_tokens = {
        token
        for token in _normalized_topic_tokens(protocol_title)
        if token not in PROTOCOL_TITLE_STOP_TOKENS and token not in GENERIC_QUERY_TOKENS
    }
    informative_tokens = [token for token in candidate_tokens if token not in SEMANTIC_TOPIC_GENERIC_TOKENS]
    informative_ratio = len(informative_tokens) / max(len(candidate_tokens), 1)
    section_overlap = _topic_overlap_ratio(set(candidate_tokens), section_tokens)
    protocol_overlap = _topic_overlap_ratio(set(candidate_tokens), protocol_tokens)

    score = (0.45 * informative_ratio) + (0.35 * section_overlap) + (0.2 * protocol_overlap)
    if len(candidate_tokens) <= 2:
        score -= 0.06
    if candidate_tokens and candidate_tokens[0] in {"בקשה", "בקשות", "דיון"}:
        score -= 0.08
    return _round_score(_clamp_score(score))


def _sanitize_semantic_topic_label(value: str) -> str | None:
    candidate = _clean_topic_candidate(value, max_tokens=5, min_tokens=2, drop_noise_tokens=True)
    if not candidate:
        return None
    tokens = [token for token in _normalized_topic_tokens(candidate) if token not in SEMANTIC_TOPIC_STOP_TOKENS]
    tokens = _trim_topic_edge_tokens(tokens)
    if len(tokens) < 2:
        return None
    sanitized = _clean_topic_candidate(" ".join(tokens), max_tokens=4, min_tokens=2, drop_noise_tokens=True)
    return sanitized


def _is_subjectless_publication_summary(value: str) -> bool:
    normalized = normalize_for_search(value)
    if not normalized:
        return False
    for pattern in SUBJECTLESS_PUBLICATION_PATTERNS:
        if pattern.search(normalized):
            return True
    return False


def _enrich_subjectless_summary(summary_text: str, topic_name: str) -> str:
    compact = " ".join(str(summary_text or "").split()).strip()
    if not compact:
        return ""
    topic_display = _topic_display_label(topic_name)
    if normalize_for_search(topic_display) in {"", normalize_for_search("ללא תיוג סמנטי")}:
        return compact

    subject = _clean_topic_candidate(topic_display, max_tokens=4, min_tokens=2, drop_noise_tokens=True)
    if not subject:
        return compact
    subject_norm = normalize_for_search(subject)
    if not subject_norm or subject_norm in {"ללא תיוג סמנטי", "נושא כללי"}:
        return compact

    compact_norm = normalize_for_search(compact)
    if subject_norm in compact_norm:
        return compact

    suffix = f" בנושא {subject}"
    if compact.endswith("."):
        return f"{compact[:-1]}{suffix}."
    return f"{compact}{suffix}."


def _best_alternative_section_text(
    *,
    section: dict[str, Any],
    context_by_chunk: dict[str, RagContextChunk],
    blocked_texts: set[str],
) -> str | None:
    chunk_ids_raw = section.get("chunk_ids")
    if not isinstance(chunk_ids_raw, list):
        return None

    candidates: list[tuple[float, str]] = []
    seen: set[str] = set()
    for chunk_id in chunk_ids_raw:
        context = context_by_chunk.get(str(chunk_id))
        if context is None:
            continue
        source_text = (context.chunk_text or context.snippet or "").strip()
        if not source_text:
            continue

        for sentence in _extract_decision_sentences(source_text):
            compact = _sanitize_summary_for_output(_compact_summary_text(sentence, max_sentences=1, max_chars=220))
            normalized = normalize_for_search(compact)
            if not normalized or normalized in blocked_texts or normalized in seen:
                continue
            seen.add(normalized)

            score = 0.2 if _has_decision_marker(normalized) else 0.0
            if not _is_boilerplate_summary_text(normalized):
                score += 0.25
            if len(_normalized_topic_tokens(compact)) >= 3:
                score += 0.1
            score += min(0.15, len(normalized) / 900.0)
            candidates.append((score, compact))

    if not candidates:
        return None
    candidates.sort(key=lambda row: row[0], reverse=True)
    best_score, best_text = candidates[0]
    if best_score < 0.2:
        return None
    return best_text


def _is_boilerplate_summary_text(value: str) -> bool:
    normalized = normalize_for_search(value)
    if not normalized:
        return False
    for pattern in SUMMARY_BOILERPLATE_PATTERNS:
        if pattern.search(normalized):
            return True
    return False


def _is_low_quality_summary_text(value: str) -> bool:
    normalized = normalize_for_search(value)
    if not normalized:
        return True
    for pattern in LOW_QUALITY_SUMMARY_PATTERNS:
        if pattern.search(normalized):
            return True
    return False


def _sanitize_summary_for_output(value: str) -> str:
    compact = " ".join(str(value or "").split())
    if not compact:
        return ""

    cleaned = re.sub(r"^\d+\s*/\s*\d+\s*", "", compact)
    if _is_boilerplate_summary_text(cleaned):
        return "אושרה החלטה פרוצדורלית בנושא פרסום החלטות הוועדה בעיתונות."
    cleaned = re.sub(r"\bבפרוטוקול\s+מס\s*['\"׳״]?\s*[:\-]?\s*\d+(?:/\d+)?", "", cleaned)
    cleaned = re.sub(r"\bבפרוטוקול\s+מס\s*['\"׳״]?", "", cleaned)
    cleaned = re.sub(r"\bמס\s*['\"׳״]?\s*[:\-]?\s*\d+(?:/\d+)?", "", cleaned)
    cleaned = " ".join(cleaned.split()).strip(" ,;:-")
    if not cleaned:
        return ""
    if cleaned[-1] not in {".", "?", "!", ";"}:
        cleaned = f"{cleaned}."
    return cleaned


def _is_procedural_allocation_summary(*, text: str, topic_name: str) -> bool:
    topic_root, _ = _topic_path_parts(topic_name)
    root_norm = normalize_for_search(topic_root)
    if root_norm not in {normalize_for_search("הקצאות"), normalize_for_search("הסכמים")}:
        return False

    normalized = normalize_for_search(text)
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in PROCEDURAL_ALLOCATION_SUMMARY_PATTERNS)


def _nearby_protocol_contexts_for_section(
    *,
    section: dict[str, Any],
    context_by_chunk: dict[str, RagContextChunk],
) -> list[RagContextChunk]:
    chunk_ids = section.get("chunk_ids")
    if not isinstance(chunk_ids, list) or not chunk_ids:
        return []

    base_contexts = [context_by_chunk.get(str(chunk_id)) for chunk_id in chunk_ids]
    base_contexts = [context for context in base_contexts if context is not None and context.source_kind == "protocol"]
    if not base_contexts:
        return []

    by_chunk_id: dict[str, RagContextChunk] = {}
    for base in base_contexts:
        by_chunk_id[base.chunk_id] = base
        for candidate in context_by_chunk.values():
            if candidate.source_kind != "protocol":
                continue
            if int(candidate.document_id) != int(base.document_id):
                continue

            same_page = (
                base.start_page is not None
                and candidate.start_page is not None
                and abs(int(base.start_page) - int(candidate.start_page)) <= 1
            )
            nearby_index = (
                base.chunk_index is not None
                and candidate.chunk_index is not None
                and abs(int(base.chunk_index) - int(candidate.chunk_index)) <= 10
            )
            if not same_page and not nearby_index:
                continue
            by_chunk_id[candidate.chunk_id] = candidate

    out = list(by_chunk_id.values())
    out.sort(
        key=lambda context: (
            int(context.start_page) if context.start_page is not None else 10**9,
            int(context.chunk_index) if context.chunk_index is not None else 10**9,
        )
    )
    return out


def _extract_request_subject_from_text(value: str) -> str | None:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        return None

    match = REQUEST_SUBJECT_RE.search(normalized)
    if match is None:
        return None

    subject = match.group(1)
    subject = re.split(r"\b(?:מורשי\s+חתימה|הבקשה\s+פורסמה|הוצבה\s+הודעה|לא\s+התקבל|פרסום\s+ראשון|פרסום\s+שני)\b", subject, maxsplit=1)[0]
    subject = " ".join(subject.split()).strip(" ,;:-")
    if not subject:
        return None
    if len(subject) > 190:
        subject = f"{subject[:187].rstrip()}..."
    return subject


def _extract_parcel_identifiers(*texts: str) -> dict[str, str]:
    combined = "\n".join(texts)
    gush_match = PARCEL_GUSH_RE.search(combined) or PARCEL_GUSH_COMPACT_RE.search(combined)
    helka_match = PARCEL_HELKA_RE.search(combined)
    migrash_match = PARCEL_MIGRASH_RE.search(combined)

    out: dict[str, str] = {}
    if gush_match:
        out["gush"] = str(gush_match.group(1))
    if helka_match:
        out["helka"] = str(helka_match.group(1))
    if migrash_match:
        out["migrash"] = str(migrash_match.group(1))
    return out


def _is_detail_metadata_text(value: str) -> bool:
    normalized = normalize_for_search(value)
    if not normalized:
        return False
    return any(
        token in normalized
        for token in {"מהות הבקשה", "גוש", "חלקה", "מגרש", "כתובת", "שטח", "שימושים", "תאור"}
    )


def _enrich_allocation_summary_with_context(
    *,
    section: dict[str, Any],
    context_by_chunk: dict[str, RagContextChunk],
) -> tuple[str | None, list[str]]:
    text_value = _as_optional_str(section.get("text")) or ""
    topic_name = _as_optional_str(section.get("topic_name")) or ""
    if not _is_procedural_allocation_summary(text=text_value, topic_name=topic_name):
        return None, []

    nearby_contexts = _nearby_protocol_contexts_for_section(section=section, context_by_chunk=context_by_chunk)
    if not nearby_contexts:
        return None, []

    subject: str | None = None
    used_chunk_ids: list[str] = []
    detail_texts: list[str] = []
    for context in nearby_contexts:
        chunk_text = " ".join((context.chunk_text or context.snippet or "").split())
        if not chunk_text:
            continue
        if _is_detail_metadata_text(chunk_text):
            detail_texts.append(chunk_text)
            if context.chunk_id not in used_chunk_ids:
                used_chunk_ids.append(context.chunk_id)
        if subject is None:
            candidate_subject = _extract_request_subject_from_text(chunk_text)
            if candidate_subject:
                subject = candidate_subject

    if not detail_texts:
        return None, []

    parcel = _extract_parcel_identifiers(*detail_texts)
    if subject:
        sentence = f"אושרה החלטת הקצאה עבור {subject}."
    else:
        sentence = "אושרה החלטת הקצאה."

    parcel_bits: list[str] = []
    if parcel.get("gush"):
        parcel_bits.append(f"גוש {parcel['gush']}")
    if parcel.get("helka"):
        parcel_bits.append(f"חלקה {parcel['helka']}")
    if parcel.get("migrash"):
        parcel_bits.append(f"מגרש {parcel['migrash']}")

    if parcel_bits:
        sentence = f"{sentence[:-1]} פרטי מקרקעין: {', '.join(parcel_bits)}."
    else:
        sentence = f"{sentence[:-1]} מזהי מקרקעין (גוש/חלקה/מגרש) לא אותרו בשורות המצוטטות."
    return sentence, used_chunk_ids


def _merge_section_chunk_ids(base_chunk_ids: Any, extra_chunk_ids: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    if isinstance(base_chunk_ids, list):
        for chunk_id in base_chunk_ids:
            key = str(chunk_id)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(key)
    for chunk_id in extra_chunk_ids:
        key = str(chunk_id)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _protocol_title_subject_hint(value: str) -> str | None:
    candidate = _clean_topic_candidate(value, max_tokens=4, min_tokens=2, drop_noise_tokens=True)
    if not candidate:
        return None
    tokens = [token for token in _normalized_topic_tokens(candidate) if token not in SEMANTIC_TOPIC_STOP_TOKENS]
    if len(tokens) < 2:
        return None
    return " ".join(tokens[:3])


def _build_protocol_topic_trees(
    *,
    summary_items: list[dict[str, Any]],
    context_by_chunk: dict[str, RagContextChunk],
    fallback_topic: str,
    cached_topic_tree: dict[str, list[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    trees: dict[str, dict[str, Any]] = {}

    for item in summary_items:
        chunk_ids = item.get("citation_chunk_ids")
        if not isinstance(chunk_ids, list) or not chunk_ids:
            continue
        first_chunk = context_by_chunk.get(chunk_ids[0])
        protocol_title = first_chunk.document_title if first_chunk else "פרוטוקול ללא כותרת"

        tree = trees.get(protocol_title)
        if tree is None:
            root_topic = _protocol_root_topic(protocol_title=protocol_title, fallback_topic=fallback_topic)
            tree = {
                "root_topic": root_topic,
                "children": [],
                "child_keys": set(),
            }
            trees[protocol_title] = tree

        if cached_topic_tree:
            child_keys: set[str] = tree["child_keys"]
            children: list[str] = tree["children"]
            root_topic = _as_optional_str(tree.get("root_topic")) or fallback_topic
            for cached_child in cached_topic_tree.get(protocol_title, []):
                candidate = _clean_topic_candidate(cached_child, max_tokens=5, min_tokens=2)
                if not candidate:
                    continue
                subtopic = _topic_subtopic_from_candidate(root_topic=root_topic, candidate=candidate) or candidate
                if not subtopic:
                    continue
                key = normalize_for_search(subtopic)
                if not key or key in child_keys:
                    continue
                child_keys.add(key)
                children.append(subtopic)
                if len(children) >= TOPIC_TREE_MAX_CHILDREN:
                    break

        root_topic = _as_optional_str(tree.get("root_topic")) or fallback_topic
        child_candidates = _protocol_child_candidates_for_item(
            item=item,
            context_by_chunk=context_by_chunk,
        )
        child_keys: set[str] = tree["child_keys"]
        children: list[str] = tree["children"]
        for candidate in child_candidates:
            subtopic = _topic_subtopic_from_candidate(
                root_topic=root_topic,
                candidate=candidate,
                drop_noise_tokens=False,
            )
            if not subtopic:
                continue
            key = normalize_for_search(subtopic)
            if not key or key in child_keys:
                continue
            child_keys.add(key)
            children.append(subtopic)
            if len(children) >= TOPIC_TREE_MAX_CHILDREN:
                break

    for tree in trees.values():
        tree.pop("child_keys", None)
    return trees


def _protocol_child_candidates_for_item(
    *,
    item: dict[str, Any],
    context_by_chunk: dict[str, RagContextChunk],
) -> list[str]:
    candidates: list[str] = []

    model_subtopic_hint = _clean_topic_candidate(
        _as_optional_str(item.get("topic_subtopic_hint_he")) or "",
        max_tokens=5,
        min_tokens=2,
        drop_noise_tokens=False,
    )
    if model_subtopic_hint:
        candidates.append(model_subtopic_hint)

    legacy_topic_hint = _clean_topic_candidate(
        _as_optional_str(item.get("topic_hint_he")) or _as_optional_str(item.get("topic_name_he")) or "",
        max_tokens=5,
        min_tokens=2,
        drop_noise_tokens=False,
    )
    if legacy_topic_hint:
        candidates.append(legacy_topic_hint)

    chunk_ids = item.get("citation_chunk_ids")
    if isinstance(chunk_ids, list):
        for chunk_id in chunk_ids:
            context = context_by_chunk.get(chunk_id)
            if context is None:
                continue
            for label in context.semantic_topic_labels:
                candidate = _clean_topic_candidate(label, max_tokens=5, min_tokens=2)
                if candidate:
                    candidates.append(candidate)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = normalize_for_search(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _resolve_topic_for_summary_item(
    *,
    item: dict[str, Any],
    protocol_tree: dict[str, Any],
    fallback_topic: str,
    context_by_chunk: dict[str, RagContextChunk],
    document_id: int | None,
    protocol_subject_anchors: dict[int, list[str]] | None,
) -> tuple[str, str, float]:
    root_topic = _as_optional_str(protocol_tree.get("root_topic")) or fallback_topic
    root_topic = _clean_topic_candidate(root_topic, max_tokens=6, min_tokens=1) or fallback_topic
    root_is_generic = _is_generic_root_topic(root_topic)
    subject_anchor = _item_subject_anchor(
        item=item,
        context_by_chunk=context_by_chunk,
        root_topic=root_topic,
        document_id=document_id,
        protocol_subject_anchors=protocol_subject_anchors,
    )
    subject_subtopic = _subject_subtopic_from_anchor(root_topic=root_topic, subject_anchor=subject_anchor)
    summary_text = _as_optional_str(item.get("summary_he")) or ""
    decision_text = summary_text

    model_subtopic_hint = _clean_topic_candidate(
        _as_optional_str(item.get("topic_subtopic_hint_he")) or "",
        max_tokens=5,
        min_tokens=2,
        drop_noise_tokens=False,
    )
    model_confidence = _as_float_0_1(item.get("topic_confidence_hint")) or 0.0
    if model_subtopic_hint:
        model_subtopic_score = _topic_candidate_score(
            candidate=model_subtopic_hint,
            decision_text=decision_text,
            root_topic=root_topic,
        )
        model_subtopic = _topic_subtopic_from_candidate(
            root_topic=root_topic,
            candidate=model_subtopic_hint,
            drop_noise_tokens=False,
        )
        if model_subtopic and (
            (model_confidence >= TOPIC_MODEL_SUBTOPIC_CONFIDENCE_MIN and model_subtopic_score >= TOPIC_TREE_CHILD_SCORE_MIN)
            or model_subtopic_score >= TOPIC_HINT_HIGH_SCORE_MIN
        ):
            return (
                f"{root_topic} > {model_subtopic}",
                "model_subtopic",
                max(model_subtopic_score, model_confidence),
            )

    topic_hint = _clean_topic_candidate(
        _as_optional_str(item.get("topic_hint_he")) or _as_optional_str(item.get("topic_name_he")) or "",
        max_tokens=5,
        min_tokens=2,
        drop_noise_tokens=False,
    )
    hint_score = 0.0
    if topic_hint:
        hint_score = _topic_candidate_score(candidate=topic_hint, decision_text=decision_text, root_topic=root_topic)
        hint_subtopic = _topic_subtopic_from_candidate(
            root_topic=root_topic,
            candidate=topic_hint,
            drop_noise_tokens=False,
        )
        if hint_subtopic and hint_score >= TOPIC_HINT_HIGH_SCORE_MIN:
            return f"{root_topic} > {hint_subtopic}", "legacy_hint_high", hint_score

    if subject_subtopic and root_is_generic:
        subject_score = _topic_candidate_score(candidate=subject_subtopic, decision_text=decision_text, root_topic=root_topic)
        if subject_score >= TOPIC_TREE_SIBLING_FALLBACK_MIN:
            return f"{root_topic} > {subject_subtopic}", "protocol_subject_anchor", subject_score
        return f"{root_topic} > {subject_subtopic}", "protocol_subject_anchor_default", 0.0

    best_tree_child: str | None = None
    best_tree_score = 0.0
    tree_children = protocol_tree.get("children")
    if isinstance(tree_children, list):
        for child in tree_children:
            child_topic = _clean_topic_candidate(child)
            if not child_topic:
                continue
            score = _topic_candidate_score(
                candidate=child_topic,
                decision_text=decision_text,
                root_topic=root_topic,
            )
            if score > best_tree_score:
                best_tree_score = score
                best_tree_child = child_topic

    if best_tree_child and best_tree_score >= TOPIC_TREE_CHILD_SCORE_MIN:
        child = _topic_subtopic_from_candidate(root_topic=root_topic, candidate=best_tree_child) or best_tree_child
        if normalize_for_search(child) != normalize_for_search(root_topic):
            return f"{root_topic} > {child}", "protocol_tree_child", best_tree_score

    if best_tree_child and best_tree_score >= TOPIC_TREE_SIBLING_FALLBACK_MIN:
        child = _topic_subtopic_from_candidate(root_topic=root_topic, candidate=best_tree_child) or best_tree_child
        if child and normalize_for_search(child) != normalize_for_search(root_topic):
            return f"{root_topic} > {child}", "protocol_tree_sibling_fallback", best_tree_score

    if isinstance(tree_children, list) and tree_children:
        default_sibling = _topic_subtopic_from_candidate(root_topic=root_topic, candidate=tree_children[0])
        if not default_sibling:
            default_sibling = _clean_topic_candidate(tree_children[0], max_tokens=5, min_tokens=2)
        if default_sibling and normalize_for_search(default_sibling) != normalize_for_search(root_topic):
            return f"{root_topic} > {default_sibling}", "protocol_tree_sibling_default", 0.0

    if topic_hint:
        hint_subtopic = _topic_subtopic_from_candidate(
            root_topic=root_topic,
            candidate=topic_hint,
            drop_noise_tokens=False,
        )
        if hint_subtopic:
            return f"{root_topic} > {hint_subtopic}", "legacy_hint_low", hint_score

    local_subtopic, local_score = _best_local_subtopic_from_decision(
        decision_text=decision_text,
        root_topic=root_topic,
    )
    if local_subtopic:
        return f"{root_topic} > {local_subtopic}", "local_subject_fallback", local_score

    return f"{root_topic} > {TOPIC_FALLBACK_CHILD_LABEL}", "protocol_root_general", 0.0


def _is_generic_root_topic(topic: str) -> bool:
    tokens = _normalized_topic_tokens(topic)
    if len(tokens) <= 1:
        return True
    if len(tokens) == 2 and all(token in TOPIC_GENERIC_ROOT_TOKENS for token in tokens):
        return True
    return False


def _item_subject_anchor(
    *,
    item: dict[str, Any],
    context_by_chunk: dict[str, RagContextChunk],
    root_topic: str,
    document_id: int | None,
    protocol_subject_anchors: dict[int, list[str]] | None,
) -> str | None:
    chunk_ids = item.get("citation_chunk_ids")
    if not isinstance(chunk_ids, list) or not chunk_ids:
        return None

    decision_text = _as_optional_str(item.get("summary_he")) or ""
    candidates: list[tuple[float, str]] = []
    seen: set[str] = set()

    if document_id is not None and protocol_subject_anchors:
        for candidate in protocol_subject_anchors.get(document_id, []):
            normalized = _clean_headline_topic_candidate(candidate, max_tokens=6, min_tokens=2)
            if not normalized:
                continue
            key = normalize_for_search(normalized)
            if not key or key in seen:
                continue
            seen.add(key)
            score = _topic_candidate_score(candidate=normalized, decision_text=decision_text, root_topic=root_topic)
            candidates.append((score + 0.08, normalized))
    for chunk_id in chunk_ids:
        context = context_by_chunk.get(chunk_id)
        if context is None:
            continue

        text_blob = (context.chunk_text or context.snippet or "").strip()
        for candidate in _headline_candidates_from_text(text_blob):
            key = normalize_for_search(candidate)
            if not key or key in seen:
                continue
            seen.add(key)
            score = _topic_candidate_score(candidate=candidate, decision_text=decision_text, root_topic=root_topic)
            candidates.append((score, candidate))

    if not candidates:
        return None
    candidates.sort(key=lambda row: row[0], reverse=True)
    best_score, best_candidate = candidates[0]
    if best_score < 0.12:
        if document_id is not None and protocol_subject_anchors and _is_generic_root_topic(root_topic):
            doc_candidates = protocol_subject_anchors.get(document_id, [])
            if doc_candidates:
                default_candidate = _clean_headline_topic_candidate(doc_candidates[0], max_tokens=6, min_tokens=2)
                if default_candidate:
                    return default_candidate
        return None
    return best_candidate


def _headline_candidates_from_text(value: str) -> list[str]:
    if not value:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for match in PROTOCOL_HEADLINE_RE.finditer(value):
        raw = match.group(1).strip()
        if len(raw) > 80:
            continue
        raw_normalized = normalize_for_search(raw)
        if not raw_normalized:
            continue
        if raw_normalized.split(" ", 1)[0] in TOPIC_RELATIONAL_EDGE_TOKENS:
            continue
        candidate = _clean_headline_topic_candidate(raw, max_tokens=6, min_tokens=2)
        if not candidate:
            continue
        key = normalize_for_search(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _subject_subtopic_from_anchor(*, root_topic: str, subject_anchor: str | None) -> str | None:
    anchor = _clean_headline_topic_candidate(subject_anchor or "", max_tokens=6, min_tokens=2)
    if not anchor:
        return None

    root_compact = " ".join((root_topic or "").split())
    candidate = anchor
    if root_compact and anchor.startswith(f"{root_compact} "):
        candidate = anchor[len(root_compact) :].strip()

    candidate = _clean_headline_topic_candidate(candidate, max_tokens=6, min_tokens=2)
    if not candidate:
        return None
    if normalize_for_search(candidate) == normalize_for_search(root_topic):
        return None
    return candidate


def _clean_headline_topic_candidate(value: str, *, max_tokens: int = 6, min_tokens: int = 2) -> str | None:
    tokens = HEBREW_HEADLINE_TOKEN_RE.findall(" ".join((value or "").split()))
    if not tokens:
        return None

    normalized_tokens: list[str] = []
    for token in tokens:
        cleaned = token.strip("\".,;:!?-()[]{}")
        if not cleaned:
            continue
        normalized_tokens.append(cleaned)

    if not normalized_tokens:
        return None

    while normalized_tokens and normalize_for_search(normalized_tokens[0]) in TOPIC_RELATIONAL_EDGE_TOKENS:
        normalized_tokens = normalized_tokens[1:]
    while normalized_tokens and normalize_for_search(normalized_tokens[-1]) in TOPIC_RELATIONAL_EDGE_TOKENS:
        normalized_tokens = normalized_tokens[:-1]
    if len(normalized_tokens) < min_tokens:
        return None

    if len(normalized_tokens[0]) >= 4 and normalized_tokens[0][0] in TOPIC_DETAIL_PREPOSITION_PREFIXES:
        normalized_tokens[0] = normalized_tokens[0][1:]
    normalized_tokens = [token for token in normalized_tokens if token]
    if len(normalized_tokens) < min_tokens:
        return None

    compact = " ".join(normalized_tokens[:max_tokens]).strip()
    if not compact:
        return None

    semantic_tokens = _normalized_topic_tokens(compact)
    if len(semantic_tokens) < min_tokens:
        return None
    if _is_generic_topic_fragment(semantic_tokens):
        return None
    return compact


def _protocol_root_topic(*, protocol_title: str, fallback_topic: str) -> str:
    title_norm = normalize_for_search(protocol_title)
    if title_norm and "בנושא" in title_norm:
        suffix = title_norm.split("בנושא", 1)[1]
        candidate = _clean_topic_candidate(suffix, max_tokens=6, min_tokens=1)
        if candidate:
            return candidate

    title_tokens = _normalized_topic_tokens(title_norm)
    filtered = [
        token
        for token in title_tokens
        if token not in PROTOCOL_TITLE_STOP_TOKENS and token not in GENERIC_QUERY_TOKENS
    ]
    if filtered:
        return " ".join(filtered[:6])

    fallback = _clean_topic_candidate(fallback_topic, max_tokens=6, min_tokens=1)
    return fallback or "נושא כללי"


def _topic_phrase_candidates_from_text(text: str) -> list[str]:
    tokens = _normalized_topic_tokens(text)
    filtered = [
        token
        for token in tokens
        if token not in GENERIC_QUERY_TOKENS
        and token not in TOPIC_NOISE_TOKENS
        and token not in PROTOCOL_TITLE_STOP_TOKENS
    ]
    if not filtered:
        return []

    limited = filtered[:18]
    candidates: list[str] = []
    seen: set[str] = set()
    for window in (3, 2):
        if len(limited) < window:
            continue
        for idx in range(0, len(limited) - window + 1):
            phrase = " ".join(limited[idx : idx + window])
            candidate = _clean_topic_candidate(phrase, max_tokens=5, min_tokens=2)
            if not candidate:
                continue
            key = normalize_for_search(candidate)
            if not key or key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
            if len(candidates) >= 10:
                return candidates
    return candidates


def _topic_subtopic_from_candidate(
    *,
    root_topic: str,
    candidate: str,
    drop_noise_tokens: bool = True,
) -> str | None:
    root_tokens = _normalized_topic_tokens(root_topic)
    root_token_set = set(root_tokens)
    candidate_tokens = _normalized_topic_tokens(candidate)
    if not candidate_tokens:
        return None

    if root_tokens and len(candidate_tokens) >= len(root_tokens) and candidate_tokens[: len(root_tokens)] == root_tokens:
        candidate_tokens = candidate_tokens[len(root_tokens) :]

    if not candidate_tokens:
        return None

    subtopic = _clean_topic_candidate(
        " ".join(candidate_tokens),
        max_tokens=5,
        min_tokens=2,
        drop_noise_tokens=drop_noise_tokens,
    )
    if not subtopic:
        return None
    if normalize_for_search(subtopic) == normalize_for_search(root_topic):
        return None
    subtopic_token_set = set(_normalized_topic_tokens(subtopic))
    if root_token_set and _token_overlap_ratio(subtopic_token_set, root_token_set) >= 0.75:
        return None
    return subtopic


def _topic_candidate_score(*, candidate: str, decision_text: str, root_topic: str) -> float:
    candidate_tokens = set(_normalized_topic_tokens(candidate))
    if not candidate_tokens:
        return 0.0

    decision_tokens = set(_normalized_topic_tokens(decision_text))
    root_tokens = set(_normalized_topic_tokens(root_topic))
    decision_overlap = _topic_overlap_ratio(candidate_tokens, decision_tokens)
    root_overlap = _topic_overlap_ratio(candidate_tokens, root_tokens) if root_tokens else 0.0
    token_count = len(candidate_tokens)
    length_quality = 1.0 if token_count <= 3 else max(0.35, 1.0 - (0.18 * (token_count - 3)))

    return _round_score(
        _clamp_score((0.7 * decision_overlap) + (0.2 * root_overlap) + (0.1 * length_quality))
    )


def _best_local_subtopic_from_decision(*, decision_text: str, root_topic: str) -> tuple[str | None, float]:
    best_topic: str | None = None
    best_score = 0.0
    for candidate in _topic_phrase_candidates_from_text(decision_text):
        subtopic = _topic_subtopic_from_candidate(root_topic=root_topic, candidate=candidate)
        if not subtopic:
            continue
        score = _topic_candidate_score(candidate=subtopic, decision_text=decision_text, root_topic=root_topic)
        if score > best_score:
            best_score = score
            best_topic = subtopic

    if best_topic and best_score >= 0.18:
        return best_topic, best_score
    return None, 0.0


def _clean_topic_candidate(
    value: str,
    *,
    max_tokens: int = TOPIC_MAX_TOKENS,
    min_tokens: int = 2,
    drop_noise_tokens: bool = True,
) -> str | None:
    raw_tokens = [
        token
        for token in _normalized_topic_tokens(value)
        if token not in GENERIC_QUERY_TOKENS
        and token not in PROTOCOL_TITLE_STOP_TOKENS
        and (not drop_noise_tokens or token not in TOPIC_NOISE_TOKENS)
    ]
    tokens = _trim_topic_edge_tokens(raw_tokens)
    tokens = _normalize_topic_granularity(tokens)
    if len(tokens) < min_tokens:
        return None

    if _is_generic_topic_fragment(tokens):
        return None
    if _is_instrument_only_topic(tokens):
        return None

    compact = " ".join(tokens[:max_tokens]).strip()
    return compact or None


def _is_generic_topic_fragment(tokens: list[str]) -> bool:
    if not tokens:
        return True
    if len(tokens) <= 2 and all(token in TOPIC_GENERIC_FRAGMENT_TOKENS for token in tokens):
        return True
    return False


def _normalize_topic_granularity(tokens: list[str]) -> list[str]:
    if not tokens:
        return []

    normalized = list(tokens)

    detail_start = _detail_tail_start_index(normalized)
    if detail_start is not None and detail_start >= 2:
        normalized = normalized[:detail_start]

    if len(normalized) > TOPIC_MAX_TOKENS:
        normalized = normalized[:TOPIC_MAX_TOKENS]

    return normalized


def _detail_tail_start_index(tokens: list[str]) -> int | None:
    for idx, token in enumerate(tokens):
        if idx < 1:
            continue
        if token in TOPIC_RELATIONAL_EDGE_TOKENS:
            return idx
        if len(token) >= 4 and token[0] in TOPIC_DETAIL_PREPOSITION_PREFIXES:
            return idx
    return None


def _is_instrument_only_topic(tokens: list[str]) -> bool:
    if not tokens:
        return True
    if len(tokens) > 3:
        return False
    return all(token in TOPIC_INSTRUMENT_ONLY_TOKENS for token in tokens)


def _topic_overlap_ratio(source_tokens: set[str], target_tokens: set[str]) -> float:
    if not source_tokens or not target_tokens:
        return 0.0

    matched = 0
    for source in source_tokens:
        if any(_topic_tokens_match(source, target) for target in target_tokens):
            matched += 1
    return _round_score(matched / max(len(source_tokens), 1))


def _topic_tokens_match(left: str, right: str) -> bool:
    if left == right:
        return True

    left_variants = _topic_token_variants(left)
    right_variants = _topic_token_variants(right)
    if left_variants.intersection(right_variants):
        return True

    for left_variant in left_variants:
        for right_variant in right_variants:
            if len(left_variant) >= 4 and len(right_variant) >= 4:
                if left_variant.startswith(right_variant) or right_variant.startswith(left_variant):
                    return True
    return False


def _topic_token_variants(token: str) -> set[str]:
    base = token.strip()
    if not base:
        return set()

    variants = {base}
    for prefix in ("ה", "ו", "ב", "ל", "כ", "מ", "ש"):
        if len(base) > 4 and base.startswith(prefix):
            variants.add(base[1:])

    endings = ("ים", "ות")
    for variant in list(variants):
        for ending in endings:
            if len(variant) > 5 and variant.endswith(ending):
                variants.add(variant[: -len(ending)])
        if len(variant) > 5 and variant.endswith("ה"):
            variants.add(variant[:-1])
        if len(variant) > 5 and variant.endswith("ת"):
            variants.add(variant[:-1])

    return {value for value in variants if value}


def _trim_topic_edge_tokens(tokens: list[str]) -> list[str]:
    if not tokens:
        return []

    start = 0
    end = len(tokens)
    while start < end and tokens[start] in TOPIC_RELATIONAL_EDGE_TOKENS:
        start += 1
    while end > start and tokens[end - 1] in TOPIC_RELATIONAL_EDGE_TOKENS:
        end -= 1

    trimmed = tokens[start:end]
    if not trimmed:
        return []
    return [token for token in trimmed if token not in TOPIC_NOISE_TOKENS]


def _normalized_topic_tokens(value: str) -> list[str]:
    normalized = normalize_for_search(value)
    if not normalized:
        return []

    tokens: list[str] = []
    for token in _hebrew_tokens(normalized):
        norm = TOPIC_TOKEN_NORMALIZATION.get(token, token)
        if not norm:
            continue
        tokens.append(norm)
    return tokens


def _compose_answer_from_sections(answer_sections: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for section in answer_sections:
        title = _as_optional_str(section.get("protocol_title")) or "פרוטוקול"
        topic = _topic_display_label(_as_optional_str(section.get("topic_name")) or "נושא כללי") or "נושא כללי"
        text = _as_optional_str(section.get("text"))
        if not text:
            continue
        parts.append(f"{title}\nנושא: {topic}\n{text}")
    return "\n\n".join(parts).strip()


def _should_use_extended_answer_as_concise(
    *,
    answer_draft: _AnswerDraft,
    answer_sections: list[dict[str, Any]],
    extended_answer: str,
) -> bool:
    if not extended_answer:
        return False
    if answer_draft.decision_items:
        return False

    section_texts = [_as_optional_str(section.get("text")) for section in answer_sections]
    non_empty_texts = [text for text in section_texts if text]
    if not non_empty_texts:
        return False
    return all(_is_placeholder_summary_text(text) for text in non_empty_texts)


def _is_placeholder_summary_text(text: str) -> bool:
    normalized = normalize_for_search(text)
    if not normalized:
        return True
    if PLACEHOLDER_SUMMARY_RE.fullmatch(normalized):
        return True
    if normalized.startswith("claim"):
        return True
    return False


def _default_topic_name(question: str) -> str:
    tokens = sorted(_primary_topic_tokens(question))
    if tokens:
        return " ".join(tokens)
    return question.strip() or "נושא כללי"


def _compose_answer_from_decision_lines(lines: list[_DecisionEvidenceLine]) -> str:
    parts: list[str] = []
    for row in lines:
        text = row.text.strip()
        if not text:
            continue
        if text[-1] not in {".", "?", "!", ";"}:
            text = f"{text}."
        parts.append(text)
    return " ".join(parts).strip()


def _reconstruct_decision_lines(
    *,
    question: str,
    retrieval_contexts: list[RagContextChunk],
    selected_claim_assessments: list[_ClaimAssessment],
    context_by_chunk: dict[str, RagContextChunk],
    decision_lines_by_chunk: dict[str, list[_DecisionLine]],
) -> list[_DecisionEvidenceLine]:
    candidates: list[tuple[float, _DecisionEvidenceLine]] = []
    seen_text: set[str] = set()

    ordered_chunk_ids = _ordered_reconstruction_chunk_ids(
        selected_claim_assessments=selected_claim_assessments,
        retrieval_contexts=retrieval_contexts,
    )
    for chunk_id in ordered_chunk_ids:
        sources: list[str] = []
        decision_rows = decision_lines_by_chunk.get(chunk_id, [])
        sources.extend(row.text for row in decision_rows)

        context = context_by_chunk.get(chunk_id)
        if context is not None:
            source_text = (context.chunk_text or context.snippet or "").strip()
            if source_text:
                sources.append(source_text)

        for source_text in sources:
            for sentence in _extract_decision_sentences(source_text):
                normalized = normalize_for_search(sentence)
                if not normalized or normalized in seen_text:
                    continue
                seen_text.add(normalized)
                score = _decision_candidate_score(question=question, candidate_text=sentence)
                candidates.append((score, _DecisionEvidenceLine(text=sentence, chunk_id=chunk_id)))

    candidates.sort(key=lambda row: row[0], reverse=True)
    out = [row[1] for row in candidates[:4]]
    return out


def _ordered_reconstruction_chunk_ids(
    *,
    selected_claim_assessments: list[_ClaimAssessment],
    retrieval_contexts: list[RagContextChunk],
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    for chunk_id in _chunk_ids_from_claim_assessments(selected_claim_assessments):
        if chunk_id in seen:
            continue
        ordered.append(chunk_id)
        seen.add(chunk_id)

    for context in retrieval_contexts:
        if context.chunk_id in seen:
            continue
        ordered.append(context.chunk_id)
        seen.add(context.chunk_id)

    return ordered


def _decision_candidate_score(*, question: str, candidate_text: str) -> float:
    question_tokens = set(_hebrew_tokens(normalize_for_search(question)))
    candidate_norm = normalize_for_search(candidate_text)
    candidate_tokens = set(_hebrew_tokens(candidate_norm))
    overlap = _token_overlap_ratio(question_tokens, candidate_tokens)
    marker_bonus = 0.2 if _has_decision_marker(candidate_norm) else 0.0
    return _round_score(_clamp_score(overlap + marker_bonus))


def _extract_decision_sentences(source_text: str) -> list[str]:
    compact = " ".join(source_text.split())
    if not compact:
        return []

    segments = [
        segment.strip()
        for segment in re.split(r"[\n\.;:!?]+", compact)
        if segment.strip()
    ]

    out: list[str] = []
    for segment in segments:
        segment_norm = normalize_for_search(segment)
        segment_has_decision_marker = _has_decision_marker(segment_norm)
        for clause in _split_decision_clauses(segment):
            normalized = normalize_for_search(clause)
            if not (
                _has_decision_marker(normalized)
                or (segment_has_decision_marker and _looks_like_decision_clause(normalized))
            ):
                continue
            out.append(clause)
    return out


def _split_decision_clauses(segment: str) -> list[str]:
    if not segment:
        return []

    clauses: list[str] = []
    first_pass = [part.strip() for part in DECISION_CLAUSE_SPLIT_RE.split(segment) if part.strip()]
    for part in first_pass or [segment]:
        second_pass = [sub.strip(" ,") for sub in DECISION_CONNECTOR_SPLIT_RE.split(part) if sub.strip(" ,")]
        clauses.extend(second_pass or [part.strip()])

    deduped: list[str] = []
    seen: set[str] = set()
    for clause in clauses:
        normalized = normalize_for_search(clause)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(clause)
    return deduped


def _looks_like_decision_clause(claim_norm: str) -> bool:
    return bool(DECISION_FALLBACK_PHRASE_RE.search(claim_norm))


def _has_decision_marker(claim_norm: str) -> bool:
    if not claim_norm:
        return False
    tokens = set(_hebrew_tokens(claim_norm))
    if tokens.intersection(STRONG_DECISION_CLAIM_MARKERS):
        return True
    return "החלטה" in claim_norm or "מאשרים" in claim_norm


def _has_discussion_marker(claim_norm: str) -> bool:
    if not claim_norm:
        return False
    tokens = set(_hebrew_tokens(claim_norm))
    return bool(tokens.intersection(DISCUSSION_CLAIM_MARKERS))


def _token_overlap_ratio(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left.intersection(right)) / max(len(left), 1)


def _contains_exact_phrase(claim_norm: str, evidence_norm: str) -> bool:
    claim_words = claim_norm.split()
    if len(claim_words) < 2:
        return False
    return claim_norm in evidence_norm


def _has_negation_conflict(claim_norm: str, evidence_norm: str) -> bool:
    claim_has_neg = " לא " in f" {claim_norm} "
    evidence_has_neg = " לא " in f" {evidence_norm} "
    return claim_has_neg != evidence_has_neg


def _clamp_score(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _claim_score_band(score: float) -> str:
    if score >= CLAIM_SCORE_HIGH_MIN:
        return "high"
    if score >= CLAIM_SCORE_MEDIUM_MIN:
        return "medium"
    return "low"


def _claim_assessment_payload(item: _ClaimAssessment) -> dict[str, Any]:
    warning = None
    if item.score < LOW_SCORE_WARNING_LIMIT:
        warning = "טענה זו בעלת התאמה נמוכה לנושא השאלה"
    return {
        "text": item.text,
        "citation_chunk_ids": list(item.citation_chunk_ids),
        "score": item.score,
        "score_band": item.score_band,
        "matched_decision_line_id": item.matched_decision_line_id,
        "matched_decision_text": item.matched_decision_text,
        "semantic_similarity_score": item.semantic_similarity_score,
        "semantic_rationale": item.semantic_rationale,
        "semantic_source": item.semantic_source,
        "topic_hits": list(item.topic_hits),
        "support_hits": list(item.support_hits),
        "secondary_context_hits": list(item.secondary_context_hits),
        "claim_topic_coverage": item.claim_topic_coverage,
        "evidence_topic_coverage": item.evidence_topic_coverage,
        "selected_for_answer": item.selected_for_answer,
        "exclusion_reason": item.exclusion_reason,
        "warning": warning,
    }


def _as_float_0_1(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0.0 or parsed > 1.0:
        return None
    return _round_score(parsed)


def _as_int_in_range(value: Any, *, min_value: int, max_value: int) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < min_value or parsed > max_value:
        return None
    return parsed


def _as_optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    compact = value.strip()
    return compact or None


def _hebrew_tokens(value: str) -> list[str]:
    return [token for token in HEBREW_TOKEN_RE.findall(value) if len(token) >= 3]


def _round_score(value: float) -> float:
    return round(float(value), 4)


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000.0, 3)


def _normalize_fallback_provider(value: str | None) -> str:
    normalized = (value or VERIFY_FALLBACK_PROVIDER_EXTERNAL).strip().casefold()
    if normalized in {VERIFY_FALLBACK_PROVIDER_EXTERNAL, "bytez", "external_verify"}:
        return VERIFY_FALLBACK_PROVIDER_EXTERNAL
    if normalized in {VERIFY_FALLBACK_PROVIDER_LOCAL, "tinyllama", "local_llm"}:
        return VERIFY_FALLBACK_PROVIDER_LOCAL
    return VERIFY_FALLBACK_PROVIDER_EXTERNAL


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(value: str | None, *, default: int, min_value: int, max_value: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(min_value, min(max_value, parsed))


def _env_float(value: str | None, *, default: float, min_value: float, max_value: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(min_value, min(max_value, parsed))
