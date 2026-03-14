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
    assert result.limitations
    assert "מבוסס על שני קטעי מקור בלבד" in result.limitations[0]
    assert result.refusal_reason_code is None
    assert len(result.claim_assessments) == 2
    assert [request["call_type"] for request in provider.requests] == ["answer", "verify"]
    assert provider.requests[0]["messages"][0]["content"].splitlines()[0] == RAG_ANSWER_PREFIX_DEFAULT
    assert provider.requests[1]["messages"][0]["content"].splitlines()[0] == RAG_VERIFY_PREFIX_DEFAULT


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
    assert result.scoring.get("fallback_verify_attempted") is True
    assert result.scoring.get("fallback_provider") == "local"
    assert result.scoring.get("fallback_overrode_deterministic_low") is True
    assert result.scoring.get("external_call_count") == 0
    assert result.claim_assessments
    assert result.claim_assessments[0]["semantic_source"] == "deterministic_similarity"


def test_rag_answering_exposes_when_external_fallback_overrides_deterministic_low_score() -> None:
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
    assert result.citations == []
    assert result.refusal_reason_code == REASON_MISSING_ATTACHMENT_EVIDENCE
    assert "אין מספיק ראיות" in (result.refusal_message_he or "")
    assert [request["call_type"] for request in provider.requests] == ["refuse"]


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
    assert "אושרה" in result.answer or "מאשרים" in result.answer
    assert result.scoring.get("decision_claim_count") == 0
    assert result.scoring.get("decision_claim_count_discussion", 0) >= 1
    assert result.scoring.get("decision_reconstruction_used") is True
    assert result.scoring.get("decision_reconstruction_count", 0) >= 2


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
    assert provider.requests[-1]["messages"][0]["content"].splitlines()[0] == RAG_REFUSE_PREFIX_DEFAULT


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
