from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from municipality.eval_rag import load_rag_eval_set, required_source_kinds
from municipality.rag_answering import REASON_MISSING_ATTACHMENT_EVIDENCE, RagAnsweringService
from municipality.rag_llm import MockRagProvider, RagLlmConfig, build_rag_llm_client
from municipality.rag_retrieval import RagRetrievalService
from municipality.search import SearchService


BOOTSTRAP_CASE_ID = "m4-t00-bootstrap-real-life-001"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_EVAL_SET_PATH = PROJECT_ROOT / "eval" / "gold" / "m4_rag_eval_set.json"


def test_m4_bootstrap_case_retrieval_returns_mixed_source_citation_context() -> None:
    eval_set = load_rag_eval_set(BOOTSTRAP_EVAL_SET_PATH)

    assert eval_set.cases
    case = eval_set.cases[0]
    assert case.case_id == BOOTSTRAP_CASE_ID
    assert required_source_kinds(case) == {"protocol", "attachment"}
    assert case.expected_grounded.answer_must_include
    assert set(case.expected_grounded.required_chunk_ids) == {evidence.chunk_id for evidence in case.required_evidence}
    assert case.expected_refusal.must_include
    assert {item.missing_source_kind for item in case.expected_refusal.cases} == {"protocol", "attachment"}

    db_path = PROJECT_ROOT / "municipality.db"
    assert db_path.exists(), "expected persisted municipality.db with M2 outputs"

    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    with Session(engine) as session:
        retrieval = RagRetrievalService(search_service=SearchService(session))
        retrieval_result = retrieval.retrieve(
            query=case.question,
            source_kinds=sorted(required_source_kinds(case)),
            semantic_mode="off",
            top_k=max(case.top_k, 8),
        )

        hit_by_chunk_id = {hit.chunk_id: hit for hit in retrieval_result.contexts}
        found_source_kinds: set[str] = set()

        for evidence in case.required_evidence:
            hit = hit_by_chunk_id.get(evidence.chunk_id)
            assert hit is not None, f"required bootstrap chunk not retrieved: {evidence.chunk_id}"
            assert hit.source_kind == evidence.source_kind
            assert hit.document_id == evidence.document_id
            assert hit.citation == evidence.citation_label
            assert hit.document_title
            assert hit.document_url
            assert hit.start_page is not None
            assert hit.end_page is not None
            found_source_kinds.add(hit.source_kind)

    engine.dispose()

    assert found_source_kinds == {"protocol", "attachment"}


def test_m4_bootstrap_missing_mixed_source_evidence_returns_refusal() -> None:
    eval_set = load_rag_eval_set(BOOTSTRAP_EVAL_SET_PATH)
    case = eval_set.cases[0]

    db_path = PROJECT_ROOT / "municipality.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    with Session(engine) as session:
        retrieval = RagRetrievalService(search_service=SearchService(session))
        retrieval_result = retrieval.retrieve(
            query=case.question,
            source_kinds=["protocol"],
            semantic_mode="off",
            top_k=max(case.top_k, 8),
        )

        provider = MockRagProvider(
            responses_by_call_type={
                "refuse": '{"refusal_message_he":"אין מספיק ראיות: חסר מקור נספח."}',
            }
        )
        llm_client = build_rag_llm_client(
            config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}),
            provider=provider,
        )
        answering = RagAnsweringService(llm_client=llm_client)
        result = answering.compose(
            question=case.question,
            retrieval=retrieval_result,
            required_source_kinds=sorted(required_source_kinds(case)),
        )

    engine.dispose()

    assert result.status == "refusal"
    assert result.answer is None
    assert result.refusal_reason_code == REASON_MISSING_ATTACHMENT_EVIDENCE
    assert "אין מספיק ראיות" in (result.refusal_message_he or "")
    assert result.missing_source_kinds == ["attachment"]
