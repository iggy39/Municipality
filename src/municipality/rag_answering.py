from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from municipality.chunking import normalize_for_search
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


TOPIC_CLAUSE_SPLIT_RE = re.compile(r"\b(?:וגם|ומה|בנוסף)\b")
HEBREW_TOKEN_RE = re.compile(r"[\u0590-\u05FF]{2,}")
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
    limitations: list[str]
    refusal_reason_code: str | None = None
    refusal_message_he: str | None = None
    missing_source_kinds: list[str] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None


@dataclass(slots=True)
class _AnswerClaim:
    text: str
    citation_chunk_ids: list[str]


@dataclass(slots=True)
class _AnswerDraft:
    answer: str
    claims: list[_AnswerClaim]
    limitations: list[str]


@dataclass(slots=True)
class _VerificationDraft:
    all_supported: bool
    unsupported_count: int


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

        answer_call = self.llm_client.generate(
            call_type=RAG_CALL_ANSWER,
            instruction=(
                "Answer in Hebrew using only provided evidence. "
                "Return strict JSON object with keys: answer (string), limitations (string[]), "
                "claims (array of objects with text and citation_chunk_ids)."
            ),
            payload=_answer_payload(question=question, retrieval=retrieval),
            ask_request_id=ask_request_id,
        )
        if answer_call.error_code or not answer_call.text:
            return self._build_refusal(
                question=question,
                retrieval=retrieval,
                reason_code=REASON_ANSWER_GENERATION_FAILED,
                missing_source_kinds=[],
                provider=answer_call.provider,
                model=answer_call.model,
                ask_request_id=ask_request_id,
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
            )

        topic_mismatch_chunk_ids = _topic_mismatch_chunk_ids(
            question=question,
            used_chunk_ids=used_chunk_ids,
            context_by_chunk=context_by_chunk,
        )
        if topic_mismatch_chunk_ids:
            log_rag_event(
                "rag.answering.topic_mismatch",
                ask_request_id=ask_request_id,
                retrieval_set_id=retrieval.retrieval_set_id,
                mismatch_chunk_ids=topic_mismatch_chunk_ids,
            )
            return self._build_refusal(
                question=question,
                retrieval=retrieval,
                reason_code=REASON_TOPIC_MISMATCH_EVIDENCE,
                missing_source_kinds=[],
                provider=answer_call.provider,
                model=answer_call.model,
                ask_request_id=ask_request_id,
            )

        verify_call = self.llm_client.generate(
            call_type=RAG_CALL_VERIFY,
            instruction=(
                "Verify every claim against provided evidence. "
                "Return strict JSON object with keys: all_supported (boolean), "
                "claims (array of objects with text, supported, citation_chunk_ids)."
            ),
            payload=_verification_payload(
                question=question,
                answer_draft=answer_draft,
                retrieval=retrieval,
            ),
            ask_request_id=ask_request_id,
        )
        if verify_call.error_code or not verify_call.text:
            return self._build_refusal(
                question=question,
                retrieval=retrieval,
                reason_code=REASON_VERIFICATION_FAILED,
                missing_source_kinds=[],
                provider=verify_call.provider,
                model=verify_call.model,
                ask_request_id=ask_request_id,
            )

        verification = _parse_verification(verify_call.text, context_by_chunk)
        if verification is None:
            return self._build_refusal(
                question=question,
                retrieval=retrieval,
                reason_code=REASON_INVALID_VERIFICATION_FORMAT,
                missing_source_kinds=[],
                provider=verify_call.provider,
                model=verify_call.model,
                ask_request_id=ask_request_id,
            )
        if not verification.all_supported:
            return self._build_refusal(
                question=question,
                retrieval=retrieval,
                reason_code=REASON_UNSUPPORTED_CLAIMS,
                missing_source_kinds=[],
                provider=verify_call.provider,
                model=verify_call.model,
                ask_request_id=ask_request_id,
            )

        citations = _build_citations(retrieval.contexts, used_chunk_ids)
        if not citations:
            return self._build_refusal(
                question=question,
                retrieval=retrieval,
                reason_code=REASON_UNCITED_CLAIMS,
                missing_source_kinds=[],
                provider=verify_call.provider,
                model=verify_call.model,
                ask_request_id=ask_request_id,
            )

        missing_sources = sorted(set(required_sources) - {citation.source_kind for citation in citations})
        if missing_sources:
            return self._build_refusal(
                question=question,
                retrieval=retrieval,
                reason_code=_reason_code_for_missing_sources(missing_sources),
                missing_source_kinds=missing_sources,
                provider=verify_call.provider,
                model=verify_call.model,
                ask_request_id=ask_request_id,
            )

        result = RagAnswerResult(
            status="answer",
            answer=answer_draft.answer,
            citations=citations,
            limitations=(answer_draft.limitations or ["התשובה מוגבלת לקטעי הראיות שסופקו."]),
            provider=verify_call.provider,
            model=verify_call.model,
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
            limitations=["אין להשיב ללא ראיות מספקות."],
            refusal_reason_code=reason_code,
            refusal_message_he=refusal_message,
            missing_source_kinds=missing_source_kinds,
            provider=provider or refusal_call.provider,
            model=model or refusal_call.model,
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
        "rules": [
            "Use only listed contexts",
            "Every major claim must include citation_chunk_ids",
            "Do not output claims without citation_chunk_ids",
        ],
    }


def _verification_payload(
    *,
    question: str,
    answer_draft: _AnswerDraft,
    retrieval: RagRetrievalResult,
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
        "rules": [
            "Mark supported true only if citations back the exact claim",
            "If any claim is unsupported set all_supported=false",
        ],
    }


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

        claims.append(
            _AnswerClaim(
                text=claim_text.strip(),
                citation_chunk_ids=normalized_ids,
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


def _parse_verification(
    value: str,
    context_by_chunk: dict[str, RagContextChunk],
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
    for claim in claims_payload:
        if not isinstance(claim, dict):
            return None
        supported = claim.get("supported")
        if not isinstance(supported, bool):
            return None
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

    if all_supported_raw and unsupported_count > 0:
        return None

    return _VerificationDraft(
        all_supported=all_supported_raw,
        unsupported_count=unsupported_count,
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


def _primary_topic_tokens(question: str) -> set[str]:
    normalized_question = normalize_for_search(question)
    if not normalized_question:
        return set()

    first_clause = TOPIC_CLAUSE_SPLIT_RE.split(normalized_question, maxsplit=1)[0]
    tokens = [token for token in HEBREW_TOKEN_RE.findall(first_clause) if len(token) >= 3]
    return {token for token in tokens if token not in GENERIC_QUERY_TOKENS}
