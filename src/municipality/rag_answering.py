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
    "הסכם",
    "בהסכם",
    "בפרוטוקול",
    "פרוטוקול",
}


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
    citations: list[RagCitation]
    claim_assessments: list[dict[str, Any]]
    limitations: list[str]
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
                "citation_chunk_ids, best_decision_line_id, semantic_similarity_score, semantic_rationale)."
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

        scoring_payload = _scoring_payload()
        final_answer = answer_draft.answer.strip()
        if decision_reconstruction_used:
            final_answer = _compose_answer_from_decision_lines(reconstructed_decision_lines)
        elif dropped_claim_assessments or not final_answer:
            final_answer = _compose_answer_from_claim_assessments(selected_claim_assessments)

        result = RagAnswerResult(
            status="answer",
            answer=final_answer,
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

    return _AnswerDraft(
        answer=answer.strip(),
        claims=claims,
        limitations=limitations_list,
        all_supported_hint=all_supported_hint,
    )


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
