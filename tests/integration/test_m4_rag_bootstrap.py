from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from municipality.api import AskRequest, _run_ask
from municipality.eval_rag import evaluate_rag_eval_set, load_rag_eval_set, required_source_kinds, retrieval_source_kinds
from municipality.rag_answering import REASON_MISSING_PROTOCOL_EVIDENCE, RagAnsweringService
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
    assert required_source_kinds(case) == {"protocol"}
    assert retrieval_source_kinds(case) == {"protocol", "attachment"}
    assert case.expected_grounded.answer_must_include
    assert set(case.expected_grounded.required_chunk_ids) == {evidence.chunk_id for evidence in case.required_evidence}
    assert case.expected_refusal.must_include
    assert {item.missing_source_kind for item in case.expected_refusal.cases} == {"protocol"}
    assert eval_set.thresholds.citation_correctness_min == 1.0
    assert eval_set.thresholds.answer_correctness_min == 1.0
    assert eval_set.thresholds.refusal_correctness_min == 1.0

    db_path = PROJECT_ROOT / "municipality.db"
    assert db_path.exists(), "expected persisted municipality.db with M2 outputs"

    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    with Session(engine) as session:
        retrieval = RagRetrievalService(search_service=SearchService(session))
        retrieval_result = retrieval.retrieve(
            query=case.question,
            source_kinds=sorted(retrieval_source_kinds(case)),
            semantic_mode="off",
            top_k=max(case.top_k, 8),
        )

        hit_by_chunk_id = {hit.chunk_id: hit for hit in retrieval_result.contexts}
        found_required_source_kinds: set[str] = set()
        found_supporting_source_kinds: set[str] = set()

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
            found_required_source_kinds.add(hit.source_kind)

        for evidence in case.supporting_evidence:
            hit = hit_by_chunk_id.get(evidence.chunk_id)
            assert hit is not None, f"supporting bootstrap chunk not retrieved: {evidence.chunk_id}"
            assert hit.source_kind == evidence.source_kind
            assert hit.document_id == evidence.document_id
            assert hit.citation == evidence.citation_label
            found_supporting_source_kinds.add(hit.source_kind)

    engine.dispose()

    assert found_required_source_kinds == {"protocol"}
    assert found_supporting_source_kinds == {"attachment"}


def test_m4_bootstrap_missing_protocol_evidence_returns_refusal() -> None:
    eval_set = load_rag_eval_set(BOOTSTRAP_EVAL_SET_PATH)
    case = eval_set.cases[0]

    db_path = PROJECT_ROOT / "municipality.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    with Session(engine) as session:
        retrieval = RagRetrievalService(search_service=SearchService(session))
        retrieval_result = retrieval.retrieve(
            query=case.question,
            source_kinds=["attachment"],
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
    assert result.refusal_reason_code == REASON_MISSING_PROTOCOL_EVIDENCE
    assert "אין מספיק ראיות" in (result.refusal_message_he or "")
    assert result.missing_source_kinds == ["protocol"]


def test_m4_rag_eval_harness_scores_new_ask_outputs() -> None:
    eval_set = load_rag_eval_set(BOOTSTRAP_EVAL_SET_PATH)
    assert eval_set.cases

    case = eval_set.cases[0]
    required_chunk_ids = list(case.expected_grounded.required_chunk_ids)
    assert len(required_chunk_ids) >= 1

    claim_labels = [f"טענה מבוססת {idx + 1}" for idx in range(len(required_chunk_ids))]
    answer_text = " ".join(case.expected_grounded.answer_must_include)
    provider = MockRagProvider(
        responses_by_call_type={
            "answer": json.dumps(
                {
                    "answer": answer_text,
                    "limitations": ["מבוסס על קטעי ראיות שנשלפו"],
                    "claims": [
                        {
                            "text": claim_labels[idx],
                            "citation_chunk_ids": [chunk_id],
                        }
                        for idx, chunk_id in enumerate(required_chunk_ids)
                    ],
                },
                ensure_ascii=False,
            ),
            "verify": json.dumps(
                {
                    "all_supported": True,
                    "claims": [
                        {
                            "text": claim_labels[idx],
                            "supported": True,
                            "citation_chunk_ids": [chunk_id],
                        }
                        for idx, chunk_id in enumerate(required_chunk_ids)
                    ],
                },
                ensure_ascii=False,
            ),
            "refuse": json.dumps({"refusal_message_he": "אין מספיק ראיות"}, ensure_ascii=False),
        }
    )
    llm_client = build_rag_llm_client(
        config=RagLlmConfig.from_env({"RAG_LLM_PROVIDER": "mock"}),
        provider=provider,
    )

    db_path = PROJECT_ROOT / "municipality.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    with Session(engine) as session:

        def ask_fn(**kwargs) -> dict:
            request = AskRequest(
                question=str(kwargs.get("question") or ""),
                top_k=int(kwargs.get("top_k") or 8),
                source_types=kwargs.get("source_types"),
                required_source_types=kwargs.get("required_source_types"),
                muni=kwargs.get("muni"),
                year=kwargs.get("year"),
                topic=kwargs.get("topic"),
                semantic_mode=str(kwargs.get("semantic_mode") or "off"),
            )
            return _run_ask(request=request, db=session, llm_client=llm_client)

        summary, case_results = evaluate_rag_eval_set(eval_set=eval_set, ask_fn=ask_fn)

    engine.dispose()

    assert case_results
    assert summary.total_cases >= 1
    assert summary.bootstrap_case_id == BOOTSTRAP_CASE_ID
    assert summary.bootstrap_traceable
    assert summary.citation_correctness >= eval_set.thresholds.citation_correctness_min
    assert summary.answer_correctness >= eval_set.thresholds.answer_correctness_min
    assert summary.refusal_correctness >= eval_set.thresholds.refusal_correctness_min
    assert summary.thresholds_passed
