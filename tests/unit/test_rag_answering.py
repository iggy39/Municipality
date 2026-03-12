from __future__ import annotations

import json

from municipality.rag_answering import (
    REASON_INSUFFICIENT_EVIDENCE,
    REASON_INVALID_VERIFICATION_FORMAT,
    REASON_MISSING_ATTACHMENT_EVIDENCE,
    REASON_TOPIC_MISMATCH_EVIDENCE,
    REASON_UNCITED_CLAIMS,
    REASON_UNSUPPORTED_CLAIMS,
    RagAnsweringService,
)
from municipality.rag_llm import (
    RAG_ANSWER_PREFIX_DEFAULT,
    RAG_REFUSE_PREFIX_DEFAULT,
    RAG_VERIFY_PREFIX_DEFAULT,
    MockRagProvider,
    RagLlmConfig,
    build_rag_llm_client,
)
from municipality.rag_retrieval import RagContextChunk, RagRetrievalResult


def test_rag_answering_returns_structured_answer_with_citations_and_limitations() -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": json.dumps(
                {
                    "answer": "הוועדה אישרה צעדי בטיחות והעירייה אישרה את ההסכם.",
                    "limitations": ["מבוסס על שני קטעי מקור בלבד"],
                    "claims": [
                        {
                            "text": "הוועדה אישרה צעדי בטיחות",
                            "citation_chunk_ids": ["chunk-protocol"],
                        },
                        {
                            "text": "העירייה אישרה את ההסכם",
                            "citation_chunk_ids": ["chunk-attachment"],
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            "verify": json.dumps(
                {
                    "all_supported": True,
                    "claims": [
                        {
                            "text": "הוועדה אישרה צעדי בטיחות",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-protocol"],
                        },
                        {
                            "text": "העירייה אישרה את ההסכם",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-attachment"],
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        }
    )
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    retrieval = _retrieval_result_with_mixed_sources()
    result = service.compose(
        question="מה הוחלט?",
        retrieval=retrieval,
        required_source_kinds=["protocol", "attachment"],
    )

    assert result.status == "answer"
    assert result.answer is not None
    assert len(result.citations) == 2
    assert {row.source_kind for row in result.citations} == {"protocol", "attachment"}
    assert result.limitations == ["מבוסס על שני קטעי מקור בלבד"]
    assert result.refusal_reason_code is None
    assert [request["call_type"] for request in provider.requests] == ["answer", "verify"]
    assert provider.requests[0]["messages"][0]["content"].splitlines()[0] == RAG_ANSWER_PREFIX_DEFAULT
    assert provider.requests[1]["messages"][0]["content"].splitlines()[0] == RAG_VERIFY_PREFIX_DEFAULT


def test_rag_answering_refuses_when_attachment_side_is_missing() -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "refuse": json.dumps(
                {
                    "refusal_message_he": "אין מספיק ראיות: חסר מקור נספח.",
                    "missing_source_kinds": ["attachment"],
                },
                ensure_ascii=False,
            )
        }
    )
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    retrieval = RagRetrievalResult(
        query="מה אושר?",
        normalized_query="מה אושר",
        top_k=5,
        retrieval_set_id="set-protocol-only",
        requested_source_kinds=["protocol"],
        contexts=[_protocol_context()],
    )
    result = service.compose(
        question="מה אושר?",
        retrieval=retrieval,
        required_source_kinds=["protocol", "attachment"],
    )

    assert result.status == "refusal"
    assert result.answer is None
    assert result.citations == []
    assert result.refusal_reason_code == REASON_MISSING_ATTACHMENT_EVIDENCE
    assert "אין מספיק ראיות" in (result.refusal_message_he or "")
    assert [request["call_type"] for request in provider.requests] == ["refuse"]
    assert provider.requests[0]["messages"][0]["content"].splitlines()[0] == RAG_REFUSE_PREFIX_DEFAULT


def test_rag_answering_refuses_when_claim_has_unknown_citation_chunk() -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": json.dumps(
                {
                    "answer": "טיוטה",
                    "claims": [
                        {
                            "text": "טענה לא מגובה",
                            "citation_chunk_ids": ["unknown-chunk"],
                        }
                    ],
                    "limitations": [],
                },
                ensure_ascii=False,
            ),
            "refuse": json.dumps(
                {
                    "refusal_message_he": "אין מספיק ראיות לטענה.",
                    "missing_source_kinds": [],
                },
                ensure_ascii=False,
            ),
        }
    )
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    result = service.compose(
        question="מה הוחלט?",
        retrieval=_retrieval_result_with_mixed_sources(),
        required_source_kinds=["protocol", "attachment"],
    )

    assert result.status == "refusal"
    assert result.refusal_reason_code == REASON_UNCITED_CLAIMS
    assert [request["call_type"] for request in provider.requests] == ["answer", "refuse"]


def test_rag_answering_refuses_when_verification_marks_unsupported_claims() -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": json.dumps(
                {
                    "answer": "טיוטה",
                    "claims": [
                        {
                            "text": "טענה",
                            "citation_chunk_ids": ["chunk-protocol"],
                        }
                    ],
                    "limitations": [],
                },
                ensure_ascii=False,
            ),
            "verify": json.dumps(
                {
                    "all_supported": False,
                    "claims": [
                        {
                            "text": "טענה",
                            "supported": False,
                            "citation_chunk_ids": ["chunk-protocol"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            "refuse": json.dumps({"refusal_message_he": "אין מספיק ראיות"}, ensure_ascii=False),
        }
    )
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    result = service.compose(
        question="מה הוחלט?",
        retrieval=_retrieval_result_with_mixed_sources(),
        required_source_kinds=["protocol", "attachment"],
    )

    assert result.status == "refusal"
    assert result.refusal_reason_code == REASON_UNSUPPORTED_CLAIMS
    assert [request["call_type"] for request in provider.requests] == ["answer", "verify", "refuse"]


def test_rag_answering_accepts_markdown_fenced_json_from_model() -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": """```json
{
  "answer": "אושרו צעדי בטיחות ואושר הסכם עירוני.",
  "limitations": ["מבוסס על שני קטעים"],
  "claims": [
    {
      "text": "אושרו צעדי בטיחות",
      "citation_chunk_ids": ["chunk-protocol"]
    },
    {
      "text": "אושר הסכם עירוני",
      "citation_chunk_ids": ["chunk-attachment"]
    }
  ]
}
```""",
            "verify": """```json
{
  "all_supported": true,
  "claims": [
    {
      "text": "אושרו צעדי בטיחות",
      "supported": true,
      "citation_chunk_ids": ["chunk-protocol"]
    },
    {
      "text": "אושר הסכם עירוני",
      "supported": true,
      "citation_chunk_ids": ["chunk-attachment"]
    }
  ]
}
```""",
        }
    )
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    result = service.compose(
        question="מה הוחלט?",
        retrieval=_retrieval_result_with_mixed_sources(),
        required_source_kinds=["protocol", "attachment"],
    )

    assert result.status == "answer"
    assert result.answer is not None
    assert {row.chunk_id for row in result.citations} == {"chunk-protocol", "chunk-attachment"}


def test_rag_answering_refuses_when_mixed_answer_cites_only_protocol_side() -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": json.dumps(
                {
                    "answer": "הוחלט לקדם מהלך בטיחות.",
                    "claims": [
                        {
                            "text": "הוחלט לקדם מהלך בטיחות",
                            "citation_chunk_ids": ["chunk-protocol"],
                        }
                    ],
                    "limitations": [],
                },
                ensure_ascii=False,
            ),
            "verify": json.dumps(
                {
                    "all_supported": True,
                    "claims": [
                        {
                            "text": "הוחלט לקדם מהלך בטיחות",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-protocol"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            "refuse": json.dumps({"refusal_message_he": "אין מספיק ראיות"}, ensure_ascii=False),
        }
    )
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    result = service.compose(
        question="מה הוחלט?",
        retrieval=_retrieval_result_with_mixed_sources(),
        required_source_kinds=["protocol", "attachment"],
    )

    assert result.status == "refusal"
    assert result.refusal_reason_code == REASON_MISSING_ATTACHMENT_EVIDENCE
    assert result.missing_source_kinds == ["attachment"]
    assert [request["call_type"] for request in provider.requests] == ["answer", "verify", "refuse"]


def test_rag_answering_refuses_ambiguous_query_without_context() -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "refuse": json.dumps(
                {
                    "refusal_message_he": "אין מספיק ראיות כדי להשיב.",
                    "missing_source_kinds": ["protocol", "attachment"],
                },
                ensure_ascii=False,
            )
        }
    )
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    retrieval = RagRetrievalResult(
        query="מה המצב?",
        normalized_query="מה המצב",
        top_k=5,
        retrieval_set_id="set-empty",
        requested_source_kinds=["protocol", "attachment"],
        contexts=[],
    )
    result = service.compose(question="מה המצב?", retrieval=retrieval)

    assert result.status == "refusal"
    assert result.refusal_reason_code == REASON_INSUFFICIENT_EVIDENCE
    assert set(result.missing_source_kinds) == {"protocol", "attachment"}
    assert [request["call_type"] for request in provider.requests] == ["refuse"]


def test_rag_answering_refuses_when_verification_cites_unknown_context_chunk() -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": json.dumps(
                {
                    "answer": "טיוטה",
                    "claims": [
                        {
                            "text": "טענה",
                            "citation_chunk_ids": ["chunk-protocol"],
                        }
                    ],
                    "limitations": [],
                },
                ensure_ascii=False,
            ),
            "verify": json.dumps(
                {
                    "all_supported": True,
                    "claims": [
                        {
                            "text": "טענה",
                            "supported": True,
                            "citation_chunk_ids": ["missing-from-retrieval"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            "refuse": json.dumps({"refusal_message_he": "אין מספיק ראיות"}, ensure_ascii=False),
        }
    )
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    result = service.compose(
        question="מה הוחלט?",
        retrieval=_retrieval_result_with_mixed_sources(),
        required_source_kinds=["protocol", "attachment"],
    )

    assert result.status == "refusal"
    assert result.refusal_reason_code == REASON_INVALID_VERIFICATION_FORMAT
    assert [request["call_type"] for request in provider.requests] == ["answer", "verify", "refuse"]


def test_rag_answering_refuses_when_mixed_sources_do_not_match_question_topic() -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": json.dumps(
                {
                    "answer": "אושרו צעדי בטיחות ובנוסף אושר הסכם כללי.",
                    "claims": [
                        {
                            "text": "אושרו צעדי בטיחות",
                            "citation_chunk_ids": ["chunk-protocol"],
                        },
                        {
                            "text": "אושר הסכם כללי",
                            "citation_chunk_ids": ["chunk-attachment"],
                        },
                    ],
                    "limitations": [],
                },
                ensure_ascii=False,
            ),
            "refuse": json.dumps({"refusal_message_he": "אין מספיק ראיות"}, ensure_ascii=False),
        }
    )
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    result = service.compose(
        question="אילו החלטות בטיחות בדרכים התקבלו ומה אישרה מועצת העיר בהסכם?",
        retrieval=_retrieval_result_with_mixed_sources(),
        required_source_kinds=["protocol", "attachment"],
    )

    assert result.status == "refusal"
    assert result.refusal_reason_code == REASON_TOPIC_MISMATCH_EVIDENCE
    assert [request["call_type"] for request in provider.requests] == ["answer", "refuse"]


def _retrieval_result_with_mixed_sources() -> RagRetrievalResult:
    return RagRetrievalResult(
        query="מה הוחלט",
        normalized_query="מה הוחלט",
        top_k=5,
        retrieval_set_id="set-mixed",
        requested_source_kinds=["protocol", "attachment"],
        contexts=[
            _protocol_context(),
            RagContextChunk(
                chunk_id="chunk-attachment",
                score=0.84,
                snippet="snippet attachment",
                citation="p.1",
                source_kind="attachment",
                document_id=22,
                document_title="נספח",
                document_url="https://example.local/attachment.pdf",
                municipality_slug="ashdod",
                meeting_external_id="meeting:1",
                start_page=1,
                end_page=1,
            ),
        ],
    )


def _protocol_context() -> RagContextChunk:
    return RagContextChunk(
        chunk_id="chunk-protocol",
        score=0.92,
        snippet="snippet protocol",
        citation="pp.2-3",
        source_kind="protocol",
        document_id=11,
        document_title="פרוטוקול",
        document_url="https://example.local/protocol.pdf",
        municipality_slug="ashdod",
        meeting_external_id="meeting:1",
        start_page=2,
        end_page=3,
    )
