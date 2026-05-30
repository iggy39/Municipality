from __future__ import annotations

from rag_eval.scripts.grade_eval import grade_one
from rag_eval.scripts.validate_eval_set import validate_one


def test_grade_one_scores_answerable_with_retrieval_and_citation_hit() -> None:
    eval_row = {
        "id": "eval_0001",
        "question_he": "מה החליטה הוועדה?",
        "expected_answer_he": "הוועדה החליטה לאשר בתנאים.",
        "source_doc_id": "doc-1",
        "source_chunk_ids": ["chunk-1"],
        "required_quote_he": "הוועדה מחליטה לאשר את הבקשה בכפוף לתנאי",
        "answer_type": "decision",
        "difficulty": "medium",
        "is_unanswerable": False,
    }
    run_row = {
        "eval_id": "eval_0001",
        "model_name": "qwen3.5:122b",
        "status": "answer",
        "answer": "הוועדה מחליטה לאשר את הבקשה בכפוף לתנאי.",
        "retrieved_chunk_ids": ["chunk-1", "chunk-2"],
        "cited_chunk_ids": ["chunk-1"],
        "latency_seconds": 1.25,
    }
    chunks_by_id = {
        "chunk-1": {
            "chunk_id": "chunk-1",
            "text": "פרוטוקול: הוועדה מחליטה לאשר את הבקשה בכפוף לתנאי נוסף.",
        }
    }

    graded = grade_one(
        eval_row=eval_row,
        run_row=run_row,
        chunks_by_id=chunks_by_id,
        judge_model="unused",
        ollama_base_url="http://unused",
        timeout_seconds=1.0,
        skip_llm_judge=True,
    )

    metrics = graded["metrics"]
    assert metrics["retrieval_hit_at_5"] is True
    assert metrics["citation_correctness"] is True
    assert metrics["latency_seconds"] == 1.25
    assert metrics["final_score"] == 0.67


def test_grade_one_rewards_unanswerable_abstention() -> None:
    eval_row = {
        "id": "eval_0002",
        "question_he": "מה מספר ההיתר?",
        "expected_answer_he": "לא נמצא מידע מספיק במסמכים שסופקו.",
        "source_doc_id": "doc-1",
        "source_chunk_ids": ["chunk-1"],
        "required_quote_he": "",
        "answer_type": "unanswerable",
        "difficulty": "medium",
        "is_unanswerable": True,
    }
    run_row = {
        "eval_id": "eval_0002",
        "model_name": "qwen3.5:122b",
        "status": "refusal",
        "answer": None,
        "retrieved_chunk_ids": ["chunk-1"],
        "cited_chunk_ids": [],
        "latency_seconds": 0.4,
        "raw_ask_response": {"refusal": {"message_he": "לא נמצא מידע מספיק במסמכים שסופקו."}},
    }

    graded = grade_one(
        eval_row=eval_row,
        run_row=run_row,
        chunks_by_id={"chunk-1": {"chunk_id": "chunk-1", "text": "אין כאן מספר היתר."}},
        judge_model="unused",
        ollama_base_url="http://unused",
        timeout_seconds=1.0,
        skip_llm_judge=True,
    )

    metrics = graded["metrics"]
    assert metrics["abstention_when_unanswerable"] is True
    assert metrics["no_hallucination"] is True
    assert metrics["final_score"] == 1.0


def test_grade_one_accepts_normalized_quote_citation_match() -> None:
    eval_row = {
        "id": "eval_0003",
        "question_he": "מה החליטה הוועדה?",
        "expected_answer_he": "הוועדה החליטה לאשר בתנאים.",
        "source_chunk_ids": ["chunk-1"],
        "required_quote_he": "הוועדה מחליטה לאשר את הבקשה בכפוף לתנאי",
        "answer_type": "decision",
        "is_unanswerable": False,
    }
    run_row = {
        "eval_id": "eval_0003",
        "model_name": "qwen3.5:122b",
        "status": "answer",
        "answer": "הוועדה מחליטה לאשר את הבקשה בכפוף לתנאי.",
        "retrieved_chunk_ids": ["chunk-1"],
        "cited_chunk_ids": ["chunk-1"],
    }

    graded = grade_one(
        eval_row=eval_row,
        run_row=run_row,
        chunks_by_id={"chunk-1": {"chunk_id": "chunk-1", "text": "הוועדה מחליטה לאשר\nאת הבקשה בכפוף לתנאי."}},
        judge_model="unused",
        ollama_base_url="http://unused",
        timeout_seconds=1.0,
        skip_llm_judge=True,
    )

    assert graded["metrics"]["citation_correctness"] is True
    assert graded["metrics"]["citation_quote_match"] == "normalized"


def test_validate_one_rejects_answerable_row_without_source_quote() -> None:
    validation = validate_one(
        row={
            "id": "eval_0004",
            "question_he": "מה החליטה הוועדה?",
            "expected_answer_he": "הוועדה החליטה לאשר.",
            "source_chunk_ids": ["chunk-1"],
            "required_quote_he": "הוועדה החליטה לדחות",
            "is_unanswerable": False,
        },
        chunks_by_id={"chunk-1": {"chunk_id": "chunk-1", "text": "הוועדה החליטה לאשר."}},
    )

    assert validation["is_valid"] is False
    assert "required_quote_not_in_source_chunks" in validation["errors"]


def test_validate_one_marks_llm_validated_when_semantic_checks_pass(monkeypatch) -> None:
    def fake_validate(**_kwargs):
        return {
            "question_answerable_from_chunk": True,
            "expected_answer_supported_by_quote": True,
            "answer_type_correct": True,
            "is_ambiguous": False,
            "reason_he": "תקין.",
        }

    monkeypatch.setattr("rag_eval.scripts.validate_eval_set._validate_expected_answer_with_llm", fake_validate)
    validation = validate_one(
        row={
            "id": "eval_0005",
            "question_he": "מה החליטה הוועדה?",
            "expected_answer_he": "הוועדה החליטה לאשר.",
            "source_chunk_ids": ["chunk-1"],
            "required_quote_he": "הוועדה החליטה לאשר",
            "is_unanswerable": False,
        },
        chunks_by_id={"chunk-1": {"chunk_id": "chunk-1", "text": "הוועדה החליטה לאשר."}},
        llm_validate=True,
    )

    assert validation["is_valid"] is True
    assert validation["quote_validation"] == "exact"
    assert validation["expected_answer_validation"] == "llm_validated"
