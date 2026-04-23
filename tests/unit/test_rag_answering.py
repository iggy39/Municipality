from __future__ import annotations

import json

import municipality.rag_answering as rag_answering
from municipality.rag_answering import (
    REASON_INSUFFICIENT_EVIDENCE,
    REASON_INVALID_VERIFICATION_FORMAT,
    LOCAL_VERIFY_PROVIDER_NAME,
    REASON_MISSING_ATTACHMENT_EVIDENCE,
    REASON_NO_DECISION_CONTENT,
    REASON_TOPIC_MISMATCH_EVIDENCE,
    REASON_UNCITED_CLAIMS,
    REASON_UNSUPPORTED_CLAIMS,
    LocalVerifyResult,
    RagAnsweringService,
)
from municipality.rag_llm import (
    RAG_ANSWER_PREFIX_DEFAULT,
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
    assert result.extended_answer is not None
    assert isinstance(result.answer_sections, list)
    assert result.answer_sections
    assert len(result.citations) == 2
    assert {row.source_kind for row in result.citations} == {"protocol", "attachment"}
    assert result.limitations
    assert "מבוסס על שני קטעי מקור בלבד" in result.limitations[0]
    assert result.refusal_reason_code is None
    assert len(result.claim_assessments) == 2
    assert [request["call_type"] for request in provider.requests] == ["answer"]
    assert provider.requests[0]["messages"][0]["content"].splitlines()[0] == RAG_ANSWER_PREFIX_DEFAULT


def test_rag_answering_skips_second_external_call_when_answer_includes_inline_verification() -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": json.dumps(
                {
                    "answer": "הוועדה אישרה צעדי בטיחות.",
                    "all_supported": True,
                    "limitations": ["מבוסס על קטע מקור יחיד"],
                    "claims": [
                        {
                            "text": "הוועדה אישרה צעדי בטיחות",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-protocol"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            "verify": json.dumps(
                {
                    "all_supported": True,
                    "claims": [
                        {
                            "text": "פינויים במתחם החרגול ברובע ז נדונו בהמשך לוועדת פינויים קודמת",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-a"],
                        },
                        {
                            "text": "אושרה הוצאת הזמנה תקציבית בסך 79000 שח להזזת מבנים של עמותת שלום ורינה",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-b"],
                        },
                        {
                            "text": "אושרה הזזת המבנים של העמותות דעת מישרים וזכרון גבריאל",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-b"],
                        },
                        {
                            "text": "לגבי מבנה בית הכנסת שלום ורינה לא ניתן להניף ולהזיז את הקראוון והצעת המחיר כוללת חיתוך אזור הספרים והציוד",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-c"],
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        }
    )
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    retrieval = RagRetrievalResult(
        query="אילו החלטות בטיחות בדרכים התקבלו?",
        normalized_query="אילו החלטות בטיחות בדרכים התקבלו",
        top_k=5,
        retrieval_set_id="set-inline-verify",
        requested_source_kinds=["protocol"],
        contexts=[
            RagContextChunk(
                chunk_id="chunk-protocol",
                score=0.91,
                snippet=".1 הוועדה אישרה צעדי בטיחות בדרכים בסביבת בתי ספר.",
                citation="pp.2-3",
                source_kind="protocol",
                document_id=11,
                document_title="פרוטוקול ועדת תשתיות מים",
                document_url="https://example.local/protocol.pdf",
                municipality_slug="ashdod",
                meeting_external_id="meeting:1",
                start_page=2,
                end_page=3,
                chunk_text=".1 הוועדה אישרה צעדי בטיחות בדרכים בסביבת בתי ספר.",
            )
        ],
    )

    result = service.compose(
        question="אילו החלטות בטיחות בדרכים התקבלו?",
        retrieval=retrieval,
        required_source_kinds=["protocol"],
    )

    assert result.status == "answer"
    assert result.answer is not None
    assert result.extended_answer is not None
    assert result.answer_sections
    assert [request["call_type"] for request in provider.requests] == ["answer"]
    assert result.scoring.get("verify_route") == "deterministic_only"
    assert result.scoring.get("fallback_verify_attempted") is False
    assert result.scoring.get("external_call_count") == 0


def test_rag_answering_uses_local_fallback_when_configured(monkeypatch) -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": json.dumps(
                {
                    "answer": "אושרה הרחבת תשתיות מים.",
                    "limitations": [],
                    "claims": [
                        {
                            "text": "אושרה הרחבת תשתיות מים",
                            "citation_chunk_ids": ["chunk-protocol"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        }
    )
    monkeypatch.setenv("RAG_VERIFY_FALLBACK_PROVIDER", "local")

    def _fake_local_verify(**kwargs):
        return LocalVerifyResult(
            provider=LOCAL_VERIFY_PROVIDER_NAME,
            model="tinyllama-test",
            text=json.dumps(
                {
                    "all_supported": True,
                    "claims": [
                        {
                            "text": "אושרה הרחבת תשתיות מים",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-protocol"],
                            "best_decision_line_id": "chunk-protocol:d1",
                            "semantic_similarity_score": 0.42,
                            "semantic_rationale": "model fallback approved claim",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            error_code=None,
            error_text=None,
        )

    monkeypatch.setattr(rag_answering, "_run_local_verify", _fake_local_verify)

    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)
    retrieval = RagRetrievalResult(
        query="אילו החלטות בטיחות בדרכים התקבלו?",
        normalized_query="אילו החלטות בטיחות בדרכים התקבלו",
        top_k=5,
        retrieval_set_id="set-local-verify",
        requested_source_kinds=["protocol"],
        contexts=[
            RagContextChunk(
                chunk_id="chunk-protocol",
                score=0.9,
                snippet=".1 הוועדה אישרה צעדי בטיחות בדרכים בסביבת בתי ספר.",
                citation="pp.2-3",
                source_kind="protocol",
                document_id=11,
                document_title="פרוטוקול ועדת תשתיות מים",
                document_url="https://example.local/protocol.pdf",
                municipality_slug="ashdod",
                meeting_external_id="meeting:1",
                start_page=2,
                end_page=3,
                chunk_text=".1 הוועדה אישרה צעדי בטיחות בדרכים בסביבת בתי ספר.",
            )
        ],
    )

    result = service.compose(
        question="אילו החלטות בטיחות בדרכים התקבלו?",
        retrieval=retrieval,
        required_source_kinds=["protocol"],
    )

    assert result.status == "answer"
    assert [request["call_type"] for request in provider.requests] == ["answer"]
    assert result.scoring.get("verify_route") == "local_verify_fallback"


def test_rag_answering_uses_extractive_decision_match_without_model_call() -> None:
    provider = MockRagProvider(responses_by_call_type={})
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    retrieval = RagRetrievalResult(
        query="מה הוחלט על תקציב החינוך?",
        normalized_query="מה הוחלט על תקציב החינוך",
        top_k=3,
        retrieval_set_id="set-extractive-decision",
        requested_source_kinds=["protocol"],
        contexts=[
            RagContextChunk(
                chunk_id="chunk-protocol",
                score=0.95,
                snippet="הוחלט לאשר את תקציב החינוך העירוני.",
                citation="p.1",
                source_kind="protocol",
                document_id=22,
                document_title="פרוטוקול ועדת חינוך",
                document_url="https://example.local/protocol.pdf",
                municipality_slug="ashdod",
                meeting_external_id="meeting:22",
                start_page=1,
                end_page=1,
                chunk_text="הוחלט לאשר את תקציב החינוך העירוני.",
            )
        ],
        debug_info={
            "top_decision_matches": [
                {
                    "decision_id": 7,
                    "similarity": 0.96,
                    "document_id": 22,
                    "document_title": "פרוטוקול ועדת חינוך",
                    "decision_text": "הוחלט לאשר את תקציב החינוך העירוני.",
                    "agenda_item": "תקציב חינוך",
                    "subject_topic_he": "תקציב חינוך",
                    "citation_chunk_ids": ["chunk-protocol"],
                }
            ]
        },
    )

    result = service.compose(
        question="מה הוחלט על תקציב החינוך?",
        retrieval=retrieval,
        required_source_kinds=["protocol"],
    )

    assert result.status == "answer"
    assert result.answer == "הוחלט לאשר את תקציב החינוך העירוני."
    assert result.provider == "DeterministicExtractive"
    assert result.scoring.get("answer_generation_route") == "extractive_decision_match"
    assert provider.requests == []
    assert result.scoring.get("external_call_count") == 0
    assert result.claim_assessments
    assert result.claim_assessments[0]["semantic_source"] == "decision_embedding_match"


def test_rag_answering_exposes_when_external_fallback_overrides_deterministic_low_score(monkeypatch) -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": json.dumps(
                {
                    "answer": "אושרה הרחבת תשתיות מים.",
                    "limitations": [],
                    "claims": [
                        {
                            "text": "אושרה הרחבת תשתיות מים",
                            "citation_chunk_ids": ["chunk-protocol"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            "verify": json.dumps(
                {
                    "all_supported": True,
                    "claims": [
                        {
                            "text": "אושרה הרחבת תשתיות מים",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-protocol"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        }
    )
    monkeypatch.setenv("RAG_VERIFY_FALLBACK_PROVIDER", "external")
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    retrieval = RagRetrievalResult(
        query="האם אושרה הרחבת תשתיות מים?",
        normalized_query="האם אושרה הרחבת תשתיות מים",
        top_k=5,
        retrieval_set_id="set-external-override",
        requested_source_kinds=["protocol"],
        contexts=[
            RagContextChunk(
                chunk_id="chunk-protocol",
                score=0.9,
                snippet=".1 אושרה הוספת תמרורים מוארים בצמתים מסוכנים.",
                citation="pp.2-3",
                source_kind="protocol",
                document_id=11,
                document_title="פרוטוקול ועדת תשתיות מים",
                document_url="https://example.local/protocol.pdf",
                municipality_slug="ashdod",
                meeting_external_id="meeting:1",
                start_page=2,
                end_page=3,
                chunk_text=".1 אושרה הוספת תמרורים מוארים בצמתים מסוכנים.",
            )
        ],
    )

    result = service.compose(
        question="האם אושרה הרחבת תשתיות מים?",
        retrieval=retrieval,
        required_source_kinds=["protocol"],
    )

    assert result.status == "answer"
    assert [request["call_type"] for request in provider.requests] == ["answer", "verify"]
    assert result.scoring.get("verify_route") == "external_verify_fallback"
    assert result.scoring.get("deterministic_would_refuse") is True
    assert result.scoring.get("fallback_provider") == "external"
    assert result.scoring.get("fallback_overrode_deterministic_low") is True
    assert result.scoring.get("fallback_supported_low_claim_indices") == [0]


def test_rag_answering_repairs_near_match_citation_chunk_ids() -> None:
    valid_chunk_id = "c11576b1f9f37ba923a0e5c544424bd2d75949ea"
    typo_chunk_id = "c11576b1f9f37ba9230e5c544424bd2d75949ea"
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": json.dumps(
                {
                    "answer": "אושרה הזמנה תקציבית לעמותה.",
                    "limitations": [],
                    "claims": [
                        {
                            "text": "אושרה הזמנה תקציבית לעמותה",
                            "citation_chunk_ids": [typo_chunk_id],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            "verify": json.dumps(
                {
                    "all_supported": True,
                    "claims": [
                        {
                            "text": "פינויים במתחם החרגול ברובע ז נדונו בהמשך לוועדת פינויים קודמת",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-a"],
                        },
                        {
                            "text": "אושרה הוצאת הזמנה תקציבית בסך 79000 שח להזזת מבנים של עמותת שלום ורינה",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-b"],
                        },
                        {
                            "text": "אושרה הזזת המבנים של העמותות דעת מישרים וזכרון גבריאל",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-b"],
                        },
                        {
                            "text": "לגבי מבנה בית הכנסת שלום ורינה לא ניתן להניף ולהזיז את הקראוון והצעת המחיר כוללת חיתוך אזור הספרים והציוד",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-c"],
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        }
    )
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    retrieval = RagRetrievalResult(
        query="מה אושר?",
        normalized_query="מה אושר",
        top_k=5,
        retrieval_set_id="set-citation-repair",
        requested_source_kinds=["protocol"],
        contexts=[
            RagContextChunk(
                chunk_id=valid_chunk_id,
                score=0.92,
                snippet="אושרה הזמנה תקציבית בסך 79,000 ₪ להזזת מבנים של עמותה.",
                citation="pp.4-5",
                source_kind="protocol",
                document_id=55,
                document_title="פרוטוקול ועדת פינויים",
                document_url="https://example.local/evac.pdf",
                municipality_slug="ashdod",
                meeting_external_id="meeting:55",
                start_page=4,
                end_page=5,
                chunk_text=".1 אושרה הזמנה תקציבית בסך 79,000 ₪ להזזת מבנים של עמותה.",
            )
        ],
    )

    result = service.compose(
        question="מה אושר?",
        retrieval=retrieval,
        required_source_kinds=["protocol"],
    )

    assert result.status == "answer"
    assert [request["call_type"] for request in provider.requests] == ["answer"]
    assert result.citations
    assert result.citations[0].chunk_id == valid_chunk_id
    assert result.scoring.get("citation_id_repair_count") == 1
    events = result.scoring.get("citation_id_repair_events")
    assert isinstance(events, list) and events
    assert events[0]["from_chunk_id"] == typo_chunk_id
    assert events[0]["to_chunk_id"] == valid_chunk_id


def test_rag_answering_claim_scores_warn_on_low_relevance_claims() -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": json.dumps(
                {
                    "answer": "התקבלו שלוש החלטות.",
                    "limitations": [],
                    "claims": [
                        {
                            "text": "להעלות בשנית את קמפיין תסתכל לנהג בעיניים מטה בטיחות בדרכים",
                            "citation_chunk_ids": ["chunk-protocol"],
                        },
                        {
                            "text": "להוסיף תמרורים מוארים לגבי איסור פניה שמאלה במקומות המתאימים",
                            "citation_chunk_ids": ["chunk-protocol"],
                        },
                        {
                            "text": "אושרה הרחבת תשתיות מים",
                            "citation_chunk_ids": ["chunk-protocol"],
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
                            "text": "להעלות בשנית את קמפיין תסתכל לנהג בעיניים מטה בטיחות בדרכים",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-protocol"],
                        },
                        {
                            "text": "להוסיף תמרורים מוארים לגבי איסור פניה שמאלה במקומות המתאימים",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-protocol"],
                        },
                        {
                            "text": "אושרה הרחבת תשתיות מים",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-protocol"],
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        }
    )
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    retrieval = RagRetrievalResult(
        query="אילו החלטות בטיחות בדרכים התקבלו?",
        normalized_query="אילו החלטות בטיחות בדרכים התקבלו",
        top_k=5,
        retrieval_set_id="set-road-safety",
        requested_source_kinds=["protocol"],
        contexts=[
            RagContextChunk(
                chunk_id="chunk-protocol",
                score=0.9,
                snippet=(
                    "החלטות: להעלות בשנית את קמפיין תסתכל לנהג בעיניים מטה בטיחות בדרכים; "
                    "להוסיף תמרורים מוארים; בדיקת בטיחות לבית הספר; מח תנועה ותחבורה."
                ),
                citation="pp.2-3",
                source_kind="protocol",
                document_id=11,
                document_title="פרוטוקול ועדת בטיחות בדרכים",
                document_url="https://example.local/protocol.pdf",
                municipality_slug="ashdod",
                meeting_external_id="meeting:1",
                start_page=2,
                end_page=3,
            )
        ],
    )

    result = service.compose(
        question="אילו החלטות בטיחות בדרכים התקבלו?",
        retrieval=retrieval,
        required_source_kinds=["protocol"],
    )

    assert result.status == "answer"
    assert len(result.claim_assessments) == 3
    low_claims = [item for item in result.claim_assessments if item["score_band"] == "low"]
    assert low_claims
    assert any("התאמה נמוכה" in (item.get("warning") or "") for item in low_claims)
    assert any("טענות בעלות התאמה נמוכה" in line for line in result.limitations)
    assert result.scoring.get("low_score_warning_applied") is True


def test_rag_answering_drops_non_decision_suffix_claim_from_final_answer() -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": json.dumps(
                {
                    "answer": "פינויים במתחם החרגול...",
                    "limitations": [],
                    "claims": [
                        {
                            "text": "פינויים במתחם החרגול ברובע ז נדונו בהמשך לוועדת פינויים קודמת",
                            "citation_chunk_ids": ["chunk-a"],
                        },
                        {
                            "text": "אושרה הוצאת הזמנה תקציבית בסך 79000 שח להזזת מבנים של עמותת שלום ורינה",
                            "citation_chunk_ids": ["chunk-b"],
                        },
                        {
                            "text": "אושרה הזזת המבנים של העמותות דעת מישרים וזכרון גבריאל",
                            "citation_chunk_ids": ["chunk-b"],
                        },
                        {
                            "text": "לגבי מבנה בית הכנסת שלום ורינה לא ניתן להניף ולהזיז את הקראוון והצעת המחיר כוללת חיתוך אזור הספרים והציוד",
                            "citation_chunk_ids": ["chunk-c"],
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
                            "text": "פינויים במתחם החרגול ברובע ז נדונו בהמשך לוועדת פינויים קודמת",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-a"],
                        },
                        {
                            "text": "אושרה הוצאת הזמנה תקציבית בסך 79000 שח להזזת מבנים של עמותת שלום ורינה",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-b"],
                        },
                        {
                            "text": "אושרה הזזת המבנים של העמותות דעת מישרים וזכרון גבריאל",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-b"],
                        },
                        {
                            "text": "לגבי מבנה בית הכנסת שלום ורינה לא ניתן להניף ולהזיז את הקראוון והצעת המחיר כוללת חיתוך אזור הספרים והציוד",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-c"],
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        }
    )
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    retrieval = RagRetrievalResult(
        query="פינויים במתחם החרגול",
        normalized_query="פינויים במתחם החרגול",
        top_k=8,
        retrieval_set_id="set-claim-selection",
        requested_source_kinds=["protocol"],
        contexts=[
            RagContextChunk(
                chunk_id="chunk-a",
                score=0.8,
                snippet="פינויים במתחם החרגול ברובע ז' בהמשך לוועדת פינויים קודמת.",
                citation="p.1",
                source_kind="protocol",
                document_id=484,
                document_title="פרוטוקול ועדת פינויים",
                document_url="https://example.local/a.pdf",
                municipality_slug="ashdod",
                meeting_external_id="meeting:1",
                start_page=1,
                end_page=1,
                chunk_text="פינויים במתחם החרגול ברובע ז' בהמשך לוועדת פינויים קודמת.",
            ),
            RagContextChunk(
                chunk_id="chunk-b",
                score=0.78,
                snippet="בהמשך לפינויים במתחם החרגול אושרה הזמנה תקציבית להזזת מבנים ואושרה הזזת מבנים לעמותות.",
                citation="p.1",
                source_kind="protocol",
                document_id=484,
                document_title="פרוטוקול ועדת פינויים",
                document_url="https://example.local/b.pdf",
                municipality_slug="ashdod",
                meeting_external_id="meeting:1",
                start_page=1,
                end_page=1,
                chunk_text="בהמשך לפינויים במתחם החרגול אושרה הזמנה תקציבית להזזת מבנים ואושרה הזזת מבנים לעמותות.",
            ),
            RagContextChunk(
                chunk_id="chunk-c",
                score=0.76,
                snippet="לגבי מבנה בית הכנסת שלום ורינה במתחם החרגול לא ניתן להניף ולהזיז את הקראוון מחשש שיתפרק.",
                citation="p.1",
                source_kind="protocol",
                document_id=484,
                document_title="פרוטוקול ועדת פינויים",
                document_url="https://example.local/c.pdf",
                municipality_slug="ashdod",
                meeting_external_id="meeting:1",
                start_page=1,
                end_page=1,
                chunk_text="לגבי מבנה בית הכנסת שלום ורינה במתחם החרגול לא ניתן להניף ולהזיז את הקראוון מחשש שיתפרק.",
            ),
        ],
    )

    result = service.compose(
        question="פינויים במתחם החרגול",
        retrieval=retrieval,
        required_source_kinds=["protocol"],
    )

    assert result.status == "answer"
    assert result.answer is not None
    assert result.extended_answer is not None
    assert "אושרה הזמנה תקציבית" in result.extended_answer
    assert len(result.answer_sections) == 2
    assert len(result.extended_answer_sections) == len(result.answer_sections)
    assert "קראוון" not in result.answer
    assert "אושרה הוצאת הזמנה תקציבית" in result.answer
    assert (result.scoring.get("dropped_claim_count") or 0) >= 1
    dropped = result.scoring.get("dropped_claim_reasons")
    assert isinstance(dropped, list) and dropped
    assert all(item.get("reason") == "non_decision_claim_when_decision_claims_present" for item in dropped)
    assert result.claim_assessments[3]["selected_for_answer"] is False


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
    assert result.extended_answer is None
    assert result.citations == []
    assert result.refusal_reason_code == REASON_MISSING_ATTACHMENT_EVIDENCE
    assert "אין מספיק ראיות" in (result.refusal_message_he or "")
    assert provider.requests == []


def test_rag_answering_reconstructs_decision_answer_when_model_outputs_only_context_claims() -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": json.dumps(
                {
                    "answer": "ועדת פינויים התקיימה ונדונו נושאים שונים.",
                    "limitations": [],
                    "claims": [
                        {
                            "text": "פינויים במתחם החרגול נדונו בוועדת פינויים 3-25",
                            "citation_chunk_ids": ["chunk-a"],
                        },
                        {
                            "text": "יו\"ר ועדת הפינויים היא סגנית מנהלת אגף נכסים",
                            "citation_chunk_ids": ["chunk-a"],
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
                            "text": "פינויים במתחם החרגול נדונו בוועדת פינויים 3-25",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-a"],
                        },
                        {
                            "text": "יו\"ר ועדת הפינויים היא סגנית מנהלת אגף נכסים",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-a"],
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        }
    )
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    retrieval = RagRetrievalResult(
        query="פינויים",
        normalized_query="פינויים",
        top_k=5,
        retrieval_set_id="set-decision-reconstruct",
        requested_source_kinds=["protocol"],
        contexts=[
            RagContextChunk(
                chunk_id="chunk-a",
                score=0.75,
                snippet="פינויים במתחם החרגול נדונו בישיבה.",
                citation="p.1",
                source_kind="protocol",
                document_id=484,
                document_title="פרוטוקול ועדת פינויים",
                document_url="https://example.local/p.pdf",
                municipality_slug="ashdod",
                meeting_external_id="meeting:1",
                start_page=1,
                end_page=1,
                chunk_text=(
                    "פינויים במתחם החרגול נדונו בישיבה. "
                    "אושרה הוצאת הזמנה תקציבית להזזת מבנים. "
                    "מאשרים הזזת מבנים של עמותות נוספות."
                ),
            )
        ],
    )

    result = service.compose(
        question="פינויים",
        retrieval=retrieval,
        required_source_kinds=["protocol"],
    )

    assert result.status == "answer"
    assert result.answer is not None
    assert result.extended_answer is not None
    assert len(result.answer_sections) >= 2
    assert len(result.extended_answer_sections) == len(result.answer_sections)
    assert "אושרה" in result.answer or "מאשרים" in result.answer
    assert result.scoring.get("decision_claim_count") == 0
    assert result.scoring.get("decision_claim_count_discussion", 0) >= 1
    assert result.scoring.get("decision_reconstruction_used") is True
    assert result.scoring.get("decision_reconstruction_count", 0) >= 2


def test_topic_resolution_prefers_protocol_child_when_model_hint_is_generic() -> None:
    summary_items = [
        {
            "summary_he": "הוחלט לקדם הכשרות ייעודיות בנושא עבור גורמי מקצוע.",
            "extended_summary_he": "הוחלט לקדם הכשרות ייעודיות בנושא עבור גורמי מקצוע.",
            "topic_name_he": "מאבק בנגע הסמים המסוכנים",
            "topic_hint_he": "מאבק בנגע הסמים המסוכנים",
            "citation_chunk_ids": ["chunk-a"],
        }
    ]
    context_by_chunk = {
        "chunk-a": RagContextChunk(
            chunk_id="chunk-a",
            score=0.88,
            snippet="הוחלט לקדם הכשרות ייעודיות בנושא עבור גורמי מקצוע.",
            citation="p.1",
            source_kind="protocol",
            document_id=30,
            document_title="פרוטוקול הועדה למאבק בנגע הסמים המסוכנים 1-25 14.05.25",
            document_url="https://example.local/p.pdf",
            municipality_slug="ashdod",
            meeting_external_id="meeting:30",
            start_page=1,
            end_page=1,
            chunk_text="הוחלט לקדם הכשרות ייעודיות בנושא עבור גורמי מקצוע.",
            semantic_topic_labels=["הכשרות ייעודיות", "שיתוף פעולה עם קופות החולים"],
        )
    }

    answer_sections, extended_sections = rag_answering._build_answer_sections(
        summary_items=summary_items,
        context_by_chunk=context_by_chunk,
        fallback_topic="מה הוחלט בעיר",
    )

    assert len(answer_sections) == 1
    assert len(extended_sections) == 1
    topic_name = answer_sections[0]["topic_name"]
    assert ">" in topic_name
    assert "הכשרות" in topic_name
    assert answer_sections[0]["topic_route"] in {
        "protocol_tree_child",
        "protocol_tree_sibling_fallback",
        "protocol_tree_sibling_default",
    }


def test_topic_resolution_uses_sibling_fallback_before_general_topic() -> None:
    summary_items = [
        {
            "summary_he": "הוחלט לקדם הכשרות ייעודיות בנושא התמכרות לתרופות מרשם עבור גורמי מקצוע.",
            "extended_summary_he": "הוחלט לקדם הכשרות ייעודיות בנושא התמכרות לתרופות מרשם עבור גורמי מקצוע.",
            "topic_name_he": "מאבק בנגע הסמים המסוכנים",
            "topic_hint_he": "מאבק בנגע הסמים המסוכנים",
            "citation_chunk_ids": ["chunk-a"],
        }
    ]
    protocol_title = "פרוטוקול הועדה למאבק בנגע הסמים המסוכנים 1-25 14.05.25"
    context_by_chunk = {
        "chunk-a": RagContextChunk(
            chunk_id="chunk-a",
            score=0.81,
            snippet="הוחלט לקדם הכשרות ייעודיות בנושא התמכרות לתרופות מרשם.",
            citation="p.2",
            source_kind="protocol",
            document_id=30,
            document_title=protocol_title,
            document_url="https://example.local/p.pdf",
            municipality_slug="ashdod",
            meeting_external_id="meeting:30",
            start_page=2,
            end_page=2,
            chunk_text="הוחלט לקדם הכשרות ייעודיות בנושא התמכרות לתרופות מרשם.",
            semantic_topic_labels=[],
        )
    }

    answer_sections, _ = rag_answering._build_answer_sections(
        summary_items=summary_items,
        context_by_chunk=context_by_chunk,
        fallback_topic="מה הוחלט בעיר",
        protocol_subject_anchors={
            30: ["הכשרת עמיתים", "שיתוף פעולה קופות החולים"],
        },
    )

    assert len(answer_sections) == 1
    topic_name = answer_sections[0]["topic_name"]
    assert topic_name != "מאבק נגע הסמים המסוכנים > החלטה כללית"
    assert answer_sections[0]["topic_route"] in {
        "protocol_subject_anchor",
        "protocol_subject_anchor_default",
        "local_subject_fallback",
    }


def test_broad_query_sections_split_per_protocol() -> None:
    answer_sections = [
        {
            "protocol_title": "פרוטוקול א",
            "topic_name": "הקצאות מקצועיות > פרסום בעיתונות",
            "text": "הוועדה אישרה ביצוע פרסום שני בעיתונות.",
            "chunk_ids": ["chunk-a", "chunk-b", "chunk-c"],
            "topic_route": "model_subtopic",
            "topic_score": 1.0,
        }
    ]
    extended_sections = [
        {
            "protocol_title": "פרוטוקול א",
            "topic_name": "הקצאות מקצועיות > פרסום בעיתונות",
            "text": "הוועדה אישרה ביצוע פרסום שני בעיתונות לאחר השלמת בדיקות.",
            "chunk_ids": ["chunk-a", "chunk-b", "chunk-c"],
            "topic_route": "model_subtopic",
            "topic_score": 1.0,
        }
    ]
    context_by_chunk = {
        "chunk-a": RagContextChunk(
            chunk_id="chunk-a",
            score=0.8,
            snippet="הוחלט לאשר פרסום שני.",
            citation="p.1",
            source_kind="protocol",
            document_id=101,
            document_title="פרוטוקול א",
            document_url="https://example.local/a.pdf",
            municipality_slug="ashdod",
            meeting_external_id="meeting:101",
            start_page=1,
            end_page=1,
            chunk_text="הוחלט לאשר פרסום שני.",
        ),
        "chunk-b": RagContextChunk(
            chunk_id="chunk-b",
            score=0.8,
            snippet="הוחלט לאשר פרסום שני.",
            citation="p.1",
            source_kind="protocol",
            document_id=102,
            document_title="פרוטוקול ב",
            document_url="https://example.local/b.pdf",
            municipality_slug="ashdod",
            meeting_external_id="meeting:102",
            start_page=1,
            end_page=1,
            chunk_text="הוחלט לאשר פרסום שני.",
        ),
        "chunk-c": RagContextChunk(
            chunk_id="chunk-c",
            score=0.8,
            snippet="הוחלט לאשר פרסום שני.",
            citation="p.1",
            source_kind="protocol",
            document_id=103,
            document_title="פרוטוקול ג",
            document_url="https://example.local/c.pdf",
            municipality_slug="ashdod",
            meeting_external_id="meeting:103",
            start_page=1,
            end_page=1,
            chunk_text="הוחלט לאשר פרסום שני.",
        ),
    }

    concise, extended, changed = rag_answering._expand_sections_by_protocol_for_broad_query(
        question="מה הוחלט בעיר?",
        answer_sections=answer_sections,
        extended_answer_sections=extended_sections,
        context_by_chunk=context_by_chunk,
    )

    assert changed is True
    assert len(concise) == 3
    assert len(extended) == 3
    assert [row["protocol_title"] for row in concise] == ["פרוטוקול א", "פרוטוקול ב", "פרוטוקול ג"]
    assert [row["chunk_ids"] for row in concise] == [["chunk-a"], ["chunk-b"], ["chunk-c"]]


def test_specific_query_sections_keep_original_grouping() -> None:
    answer_sections = [
        {
            "protocol_title": "פרוטוקול א",
            "topic_name": "פינויים > פינוי מבנים",
            "text": "אושרה הזזת מבנים לעמותות.",
            "chunk_ids": ["chunk-a", "chunk-b"],
        }
    ]
    context_by_chunk = {
        "chunk-a": RagContextChunk(
            chunk_id="chunk-a",
            score=0.8,
            snippet="אושרה הזזת מבנים.",
            citation="p.2",
            source_kind="protocol",
            document_id=101,
            document_title="פרוטוקול א",
            document_url="https://example.local/a.pdf",
            municipality_slug="ashdod",
            meeting_external_id="meeting:101",
            start_page=2,
            end_page=2,
            chunk_text="אושרה הזזת מבנים.",
        ),
        "chunk-b": RagContextChunk(
            chunk_id="chunk-b",
            score=0.8,
            snippet="אושרה הזזת מבנים נוספת.",
            citation="p.3",
            source_kind="protocol",
            document_id=102,
            document_title="פרוטוקול ב",
            document_url="https://example.local/b.pdf",
            municipality_slug="ashdod",
            meeting_external_id="meeting:102",
            start_page=3,
            end_page=3,
            chunk_text="אושרה הזזת מבנים נוספת.",
        ),
    }

    concise, extended, changed = rag_answering._expand_sections_by_protocol_for_broad_query(
        question="פינויים במתחם החרגול",
        answer_sections=answer_sections,
        extended_answer_sections=answer_sections,
        context_by_chunk=context_by_chunk,
    )

    assert changed is False
    assert concise == answer_sections
    assert extended == answer_sections


def test_topic_cleaning_reduces_over_specific_subtopic_phrase() -> None:
    cleaned = rag_answering._clean_topic_candidate("הוספת תמרורים מוארים לאיסור פניה שמאלה", min_tokens=2)

    assert cleaned is not None
    assert "לאיסור" not in cleaned
    assert cleaned in {"תמרורים מוארים", "תמרורים מוארים פניה", "הוספת תמרורים"}


def test_section_semantic_topic_prefers_more_specific_candidate() -> None:
    context_by_chunk = {
        "chunk-a": RagContextChunk(
            chunk_id="chunk-a",
            score=0.8,
            snippet="הוחלט לאשר פרסום זמני וראשון בעיתונות.",
            citation="p.2",
            source_kind="protocol",
            document_id=312,
            document_title="פרוטוקול ועדת הקצאות מקצועית מס' 1-25",
            document_url="https://example.local/a.pdf",
            municipality_slug="ashdod",
            meeting_external_id="meeting:312",
            start_page=2,
            end_page=2,
            chunk_text="הוחלט לאשר פרסום זמני וראשון בעיתונות.",
            semantic_topic_labels=[],
        )
    }
    section = {
        "protocol_title": "פרוטוקול ועדת הקצאות מקצועית מס' 1-25",
        "text": "אושר ביצוע פרסום זמני וראשון בעיתונות.",
        "chunk_ids": ["chunk-a"],
    }
    protocol_semantic_topic_labels = {
        312: [
            "בקשה להקצאה",
            "בבקשות להקצאת קרקע ומבנים",
            "הקצאת כיתת גן ילדים",
        ]
    }

    topic = rag_answering._section_semantic_topic_label(
        section=section,
        context_by_chunk=context_by_chunk,
        protocol_semantic_topic_labels=protocol_semantic_topic_labels,
    )

    assert topic in {"הקצאת כיתת גן ילדים", "הקצאת כיתת ילדים"}


def test_subjectless_publication_summary_is_enriched_with_topic() -> None:
    context_by_chunk = {
        "chunk-a": RagContextChunk(
            chunk_id="chunk-a",
            score=0.8,
            snippet="הוחלט לאשר פרסום זמני וראשון בעיתונות.",
            citation="p.2",
            source_kind="protocol",
            document_id=312,
            document_title="פרוטוקול ועדת הקצאות מקצועית מס' 1-25",
            document_url="https://example.local/a.pdf",
            municipality_slug="ashdod",
            meeting_external_id="meeting:312",
            start_page=2,
            end_page=2,
            chunk_text="הוחלט לאשר פרסום זמני וראשון בעיתונות.",
            semantic_topic_labels=[],
        )
    }
    concise = [
        {
            "protocol_title": "פרוטוקול ועדת הקצאות מקצועית מס' 1-25",
            "topic_name": "נושא כללי",
            "text": "אושר ביצוע פרסום זמני וראשון בעיתונות.",
            "chunk_ids": ["chunk-a"],
        }
    ]

    enriched, _, semantic_changed, _ = rag_answering._enforce_semantic_topics_and_section_uniqueness(
        question="מה הוחלט בעיר?",
        answer_sections=concise,
        extended_answer_sections=concise,
        context_by_chunk=context_by_chunk,
        protocol_semantic_topic_labels={312: ["הקצאת כיתת גן ילדים"]},
    )

    assert semantic_changed is True
    assert "בנושא הקצאת כיתת" in enriched[0]["text"]


def test_resolve_section_topic_name_prefers_object_first_agreements() -> None:
    context_by_chunk = {
        "chunk-a": RagContextChunk(
            chunk_id="chunk-a",
            score=0.9,
            snippet="מאשרים הכנת הסכם רשות לתקופה של 5 שנים לעמותה.",
            citation="p.20",
            source_kind="protocol",
            document_id=470,
            document_title="פרוטוקול ועדת הקצאות מקצועית מס' 8-25",
            document_url="https://example.local/p.pdf",
            municipality_slug="ashdod",
            meeting_external_id="meeting:470",
            start_page=20,
            end_page=20,
            chunk_text="מאשרים הכנת הסכם רשות לתקופה של 5 שנים לעמותה.",
            semantic_topic_labels=[],
        )
    }
    section = {
        "protocol_title": "פרוטוקול ועדת הקצאות מקצועית מס' 8-25",
        "topic_name": "נושא כללי",
        "text": "אושרה הכנת הסכם רשות לתקופה של 5 שנים.",
        "chunk_ids": ["chunk-a"],
    }

    topic_name = rag_answering._resolve_section_topic_name(
        section=section,
        semantic_topic=None,
        context_by_chunk=context_by_chunk,
    )

    assert topic_name.startswith("הסכמים >")
    assert "הסכם רשות" in topic_name


def test_enforce_semantic_topics_never_returns_placeholder_topic() -> None:
    context_by_chunk = {
        "chunk-a": RagContextChunk(
            chunk_id="chunk-a",
            score=0.9,
            snippet="מאשרים הכנת הסכם רשות לתקופה של 5 שנים לעמותה.",
            citation="p.20",
            source_kind="protocol",
            document_id=470,
            document_title="פרוטוקול ועדת הקצאות מקצועית מס' 8-25",
            document_url="https://example.local/p.pdf",
            municipality_slug="ashdod",
            meeting_external_id="meeting:470",
            start_page=20,
            end_page=20,
            chunk_text="מאשרים הכנת הסכם רשות לתקופה של 5 שנים לעמותה.",
            semantic_topic_labels=[],
        )
    }
    concise = [
        {
            "protocol_title": "פרוטוקול ועדת הקצאות מקצועית מס' 8-25",
            "topic_name": "נושא כללי",
            "text": "אושרה הכנת הסכם רשות לתקופה של 5 שנים.",
            "chunk_ids": ["chunk-a"],
        }
    ]

    enriched, _, _, _ = rag_answering._enforce_semantic_topics_and_section_uniqueness(
        question="מה הוחלט בעיר?",
        answer_sections=concise,
        extended_answer_sections=concise,
        context_by_chunk=context_by_chunk,
        protocol_semantic_topic_labels={470: []},
    )

    assert "ללא תיוג סמנטי" not in str(enriched[0]["topic_name"])
    assert ">" in str(enriched[0]["topic_name"])


def test_enrich_allocation_procedural_summary_with_parcel_details() -> None:
    decision_chunk = RagContextChunk(
        chunk_id="chunk-decision",
        score=0.8,
        snippet="החלטות: מאשרים החלטת הועדה המקצועית להקצאות קרקע בפרוטוקול מס' 4/25.",
        citation="p.2",
        source_kind="protocol",
        document_id=376,
        document_title="פרוטוקול ועדת משנה להקצאות קרקע מס' 4-25",
        document_url="https://example.local/p.pdf",
        municipality_slug="ashdod",
        meeting_external_id="meeting:376",
        start_page=2,
        end_page=2,
        chunk_index=14,
        chunk_text="החלטות: מאשרים החלטת הועדה המקצועית להקצאות קרקע בפרוטוקול מס' 4/25.",
    )
    detail_chunk = RagContextChunk(
        chunk_id="chunk-detail",
        score=0.5,
        snippet="מהות הבקשה: בקשה להקצאת קרקע בשטח של 1,800 מ\"ר למטרת הקמת בית כנסת.",
        citation="p.2",
        source_kind="protocol",
        document_id=376,
        document_title="פרוטוקול ועדת משנה להקצאות קרקע מס' 4-25",
        document_url="https://example.local/p.pdf",
        municipality_slug="ashdod",
        meeting_external_id="meeting:376",
        start_page=2,
        end_page=2,
        chunk_index=13,
        chunk_text="מהות הבקשה: בקשה להקצאת קרקע בשטח של 1,800 מ\"ר למטרת הקמת בית כנסת.",
    )
    parcel_chunk = RagContextChunk(
        chunk_id="chunk-parcel",
        score=0.5,
        snippet="חלקה 94 :מגרש132 : גושים וחלקות: גוש2023",
        citation="p.2",
        source_kind="protocol",
        document_id=376,
        document_title="פרוטוקול ועדת משנה להקצאות קרקע מס' 4-25",
        document_url="https://example.local/p.pdf",
        municipality_slug="ashdod",
        meeting_external_id="meeting:376",
        start_page=2,
        end_page=2,
        chunk_index=9,
        chunk_text="חלקה 94 :מגרש132 : גושים וחלקות: גוש2023",
    )

    section = {
        "protocol_title": "פרוטוקול ועדת משנה להקצאות קרקע מס' 4-25",
        "topic_name": "הקצאות > אישור הקצאה",
        "text": "אושרה החלטה פרוצדורלית בנושא פרסום החלטות הוועדה בעיתונות.",
        "chunk_ids": ["chunk-decision"],
    }
    context_by_chunk = {
        "chunk-decision": decision_chunk,
        "chunk-detail": detail_chunk,
        "chunk-parcel": parcel_chunk,
    }

    enriched_text, used_chunk_ids = rag_answering._enrich_allocation_summary_with_context(
        section=section,
        context_by_chunk=context_by_chunk,
    )

    assert enriched_text is not None
    assert "בקשה להקצאת קרקע" in enriched_text
    assert "גוש 2023" in enriched_text
    assert "חלקה 94" in enriched_text
    assert "מגרש 132" in enriched_text
    assert "chunk-detail" in used_chunk_ids
    assert "chunk-parcel" in used_chunk_ids


def test_enrich_allocation_procedural_summary_adds_missing_parcel_note() -> None:
    decision_chunk = RagContextChunk(
        chunk_id="chunk-decision",
        score=0.8,
        snippet="מאשרים החלטת הועדה המקצועית להקצאות קרקע.",
        citation="p.16",
        source_kind="protocol",
        document_id=472,
        document_title="פרוטוקול ועדת משנה להקצאות קרקע מס' 7-25",
        document_url="https://example.local/p.pdf",
        municipality_slug="ashdod",
        meeting_external_id="meeting:472",
        start_page=16,
        end_page=16,
        chunk_index=158,
        chunk_text="מאשרים החלטת הועדה המקצועית להקצאות קרקע.",
    )
    detail_chunk = RagContextChunk(
        chunk_id="chunk-detail",
        score=0.5,
        snippet="מהות הבקשה: בקשת העמותה להקצאת שתי כיתות גני ילדים בקומת הקרקע.",
        citation="p.16",
        source_kind="protocol",
        document_id=472,
        document_title="פרוטוקול ועדת משנה להקצאות קרקע מס' 7-25",
        document_url="https://example.local/p.pdf",
        municipality_slug="ashdod",
        meeting_external_id="meeting:472",
        start_page=16,
        end_page=16,
        chunk_index=157,
        chunk_text="מהות הבקשה: בקשת העמותה להקצאת שתי כיתות גני ילדים בקומת הקרקע.",
    )
    section = {
        "protocol_title": "פרוטוקול ועדת משנה להקצאות קרקע מס' 7-25",
        "topic_name": "הקצאות > אישור הקצאה",
        "text": "מאשרים החלטת הועדה המקצועית להקצאות קרקע.",
        "chunk_ids": ["chunk-decision"],
    }

    enriched_text, _ = rag_answering._enrich_allocation_summary_with_context(
        section=section,
        context_by_chunk={"chunk-decision": decision_chunk, "chunk-detail": detail_chunk},
    )

    assert enriched_text is not None
    assert "מזהי מקרקעין" in enriched_text


def test_resolve_section_topic_name_prefers_persisted_decision_request_topic() -> None:
    context_by_chunk = {
        "chunk-a": RagContextChunk(
            chunk_id="chunk-a",
            score=0.9,
            snippet="אושרה החלטה פרוצדורלית בנושא פרסום החלטות הוועדה בעיתונות.",
            citation="p.2",
            source_kind="protocol",
            document_id=331,
            document_title="פרוטוקול ועדת הקצאות מקצועית מס' 2-25",
            document_url="https://example.local/p.pdf",
            municipality_slug="ashdod",
            meeting_external_id="meeting:331",
            start_page=2,
            end_page=2,
            chunk_text="אושרה החלטה פרוצדורלית בנושא פרסום החלטות הוועדה בעיתונות.",
            semantic_topic_labels=[],
        )
    }
    section = {
        "protocol_title": "פרוטוקול ועדת הקצאות מקצועית מס' 2-25",
        "topic_name": "הקצאות > ביטול הקצאה",
        "text": "אושרה החלטה פרוצדורלית בנושא פרסום החלטות הוועדה בעיתונות.",
        "chunk_ids": ["chunk-a"],
    }

    topic_name = rag_answering._resolve_section_topic_name(
        section=section,
        semantic_topic="ביטול הקצאה",
        context_by_chunk=context_by_chunk,
        decision_request_context_by_chunk={
            "chunk-a": {
                "request_subject_he": 'עמותת "קול הקריות" קיבלה הקצאה במקלט "החבל" ומבקשת החלפה למקלט "לגונה"',
                "subject_topic_he": "החלפת הקצאה למקלט",
                "confidence": 0.92,
            }
        },
    )

    assert topic_name == "הקצאות > החלפת הקצאה למקלט"


def test_enrich_allocation_summary_prefers_persisted_decision_request_context() -> None:
    context_by_chunk = {
        "chunk-a": RagContextChunk(
            chunk_id="chunk-a",
            score=0.8,
            snippet="הועדה אישרה ביצוע פרסום שני בעיתונות.",
            citation="p.5",
            source_kind="protocol",
            document_id=331,
            document_title="פרוטוקול ועדת הקצאות מקצועית מס' 2-25",
            document_url="https://example.local/p.pdf",
            municipality_slug="ashdod",
            meeting_external_id="meeting:331",
            start_page=5,
            end_page=5,
            chunk_index=12,
            chunk_text="הועדה אישרה ביצוע פרסום שני בעיתונות.",
            semantic_topic_labels=[],
        )
    }
    section = {
        "protocol_title": "פרוטוקול ועדת הקצאות מקצועית מס' 2-25",
        "topic_name": "הקצאות > החלפת הקצאה למקלט",
        "text": "אושרה החלטה פרוצדורלית בנושא פרסום החלטות הוועדה בעיתונות.",
        "chunk_ids": ["chunk-a"],
    }

    enriched_text, used_chunk_ids = rag_answering._enrich_allocation_summary_with_context(
        section=section,
        context_by_chunk=context_by_chunk,
        decision_request_context_by_chunk={
            "chunk-a": {
                "request_subject_he": 'עמותת "קול הקריות" קיבלה הקצאה במקלט "החבל" ומבקשת החלפה למקלט "לגונה"',
                "subject_topic_he": "החלפת הקצאה למקלט",
                "decision_text": "הועדה מאשרת ביצוע פרסום שני בעיתונות.",
                "gush": "2066",
                "helka": "428",
                "migrash": None,
                "source_chunk_ids": ["chunk-a", "chunk-subject"],
                "confidence": 0.95,
            }
        },
    )

    assert enriched_text is not None
    assert "אושר פרסום בעיתונות עבור" in enriched_text
    assert "מקלט \"לגונה\"" in enriched_text
    assert "גוש 2066" in enriched_text
    assert "חלקה 428" in enriched_text
    assert used_chunk_ids == ["chunk-a", "chunk-subject"]


def test_broad_query_rewrites_agreement_summary_from_decision_context() -> None:
    context_by_chunk = {
        "chunk-a": RagContextChunk(
            chunk_id="chunk-a",
            score=0.8,
            snippet="החלטות: מאשרים הכנת הסכם רשות לתקופה של 5 שנים.",
            citation="p.20",
            source_kind="protocol",
            document_id=470,
            document_title="פרוטוקול ועדת הקצאות מקצועית מס' 8-25",
            document_url="https://example.local/p.pdf",
            municipality_slug="ashdod",
            meeting_external_id="meeting:470",
            start_page=20,
            end_page=20,
            chunk_index=211,
            chunk_text="החלטות: מאשרים הכנת הסכם רשות לתקופה של 5 שנים.",
            semantic_topic_labels=[],
        )
    }
    concise = [
        {
            "protocol_title": "פרוטוקול ועדת הקצאות מקצועית מס' 8-25",
            "topic_name": "הסכמים > הסכם רשות לעמותה",
            "text": "אושרה הכנת הסכם רשות לתקופה של 5 שנים.",
            "chunk_ids": ["chunk-a"],
        }
    ]

    enriched, _, _, _ = rag_answering._enforce_semantic_topics_and_section_uniqueness(
        question="מה הוחלט בעיר?",
        answer_sections=concise,
        extended_answer_sections=concise,
        context_by_chunk=context_by_chunk,
        protocol_semantic_topic_labels={470: ["הקצאת כיתות גן ילדים"]},
        decision_request_context_by_chunk={
            "chunk-a": {
                "request_subject_he": "בקשה למתן רשות שימוש ב-2 כיתות גני ילדים להפעלה",
                "subject_topic_he": "הקצאת כיתות גן ילדים",
                "decision_text": "מאשרים הכנת הסכם רשות לתקופה של 5 שנים.",
                "source_chunk_ids": ["chunk-a", "chunk-subject"],
                "confidence": 0.98,
            }
        },
    )

    assert "מתן רשות שימוש" in enriched[0]["text"]
    assert enriched[0]["chunk_ids"] == ["chunk-a", "chunk-subject"]


def test_compact_request_subject_for_summary_keeps_core_subject_only() -> None:
    compact = rag_answering._compact_request_subject_for_summary(
        "בקשה למתן רשות שימוש ב 2-כיתות גני ילדים להפעלה. מבנים אלו נמסרו לעירייה במסגרת התחייבות יזם י.שפץ."
    )

    assert compact == "מתן רשות שימוש ב 2-כיתות גני ילדים להפעלה"


def test_compact_request_subject_for_summary_skips_agreement_preamble() -> None:
    compact = rag_answering._compact_request_subject_for_summary(
        "לעמותה קיים הסכם רשות שימוש מס' 6460. העמותה מבקשת להסדיר רשות שימוש לתקופה נוספת ל 6-גני ילדים המופעלים ע\"י העמותה מזה שנים."
    )

    assert compact == "הסדרת רשות שימוש לתקופה נוספת ל 6-גני ילדים המופעלים ע\"י העמותה מזה שנים"


def test_rag_answering_refuses_when_no_decision_content_exists() -> None:
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": json.dumps(
                {
                    "answer": "הישיבה התקיימה בהשתתפות בעלי תפקידים.",
                    "limitations": [],
                    "claims": [
                        {
                            "text": "ועדת פינויים התקיימה ביום 18-11-25",
                            "citation_chunk_ids": ["chunk-a"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            "verify": json.dumps(
                {
                    "all_supported": True,
                    "claims": [
                        {
                            "text": "ועדת פינויים התקיימה ביום 18-11-25",
                            "supported": True,
                            "citation_chunk_ids": ["chunk-a"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        }
    )
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    retrieval = RagRetrievalResult(
        query="פינויים",
        normalized_query="פינויים",
        top_k=5,
        retrieval_set_id="set-no-decision-content",
        requested_source_kinds=["protocol"],
        contexts=[
            RagContextChunk(
                chunk_id="chunk-a",
                score=0.72,
                snippet="ועדת פינויים מספר 3-25 התקיימה ביום 18-11-25.",
                citation="p.1",
                source_kind="protocol",
                document_id=484,
                document_title="פרוטוקול ועדת פינויים",
                document_url="https://example.local/p.pdf",
                municipality_slug="ashdod",
                meeting_external_id="meeting:1",
                start_page=1,
                end_page=1,
                chunk_text="ועדת פינויים מספר 3-25 התקיימה ביום 18-11-25. יו\"ר הוועדה היא סגנית מנהלת אגף נכסים.",
            )
        ],
    )

    result = service.compose(
        question="פינויים",
        retrieval=retrieval,
        required_source_kinds=["protocol"],
    )

    assert result.status == "refusal"
    assert result.refusal_reason_code == REASON_NO_DECISION_CONTENT
    assert result.scoring.get("decision_required") is True
    assert result.scoring.get("decision_claim_count") == 0
    assert result.scoring.get("decision_reconstruction_used") is False
    assert [request["call_type"] for request in provider.requests] == ["answer"]


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
    assert [request["call_type"] for request in provider.requests] == ["answer"]


def test_rag_answering_refuses_when_verification_marks_unsupported_claims(monkeypatch) -> None:
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
    monkeypatch.setenv("RAG_VERIFY_FALLBACK_PROVIDER", "external")
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    result = service.compose(
        question="מה הוחלט?",
        retrieval=_retrieval_result_with_mixed_sources(),
        required_source_kinds=["protocol", "attachment"],
    )

    assert result.status == "refusal"
    assert result.refusal_reason_code == REASON_UNSUPPORTED_CLAIMS
    assert [request["call_type"] for request in provider.requests] == ["answer", "verify"]


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
    assert [request["call_type"] for request in provider.requests] == ["answer"]


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
    assert provider.requests == []


def test_rag_answering_refuses_when_verification_cites_unknown_context_chunk(monkeypatch) -> None:
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
    monkeypatch.setenv("RAG_VERIFY_FALLBACK_PROVIDER", "external")
    client = build_rag_llm_client(config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}), provider=provider)
    service = RagAnsweringService(llm_client=client)

    result = service.compose(
        question="מה הוחלט?",
        retrieval=_retrieval_result_with_mixed_sources(),
        required_source_kinds=["protocol", "attachment"],
    )

    assert result.status == "refusal"
    assert result.refusal_reason_code == REASON_INVALID_VERIFICATION_FORMAT
    assert [request["call_type"] for request in provider.requests] == ["answer", "verify"]


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
    assert [request["call_type"] for request in provider.requests] == ["answer"]


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
