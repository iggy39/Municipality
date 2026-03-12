from __future__ import annotations

from municipality.rag_observability import (
    DEFAULT_RAG_AUDIT_SAMPLE_RATE,
    audit_sample_rate,
    build_retrieval_set_id,
    hash_text,
    should_sample_audit,
)


def test_retrieval_set_id_is_stable_for_same_inputs() -> None:
    left = build_retrieval_set_id(
        normalized_query="מה אושר",
        requested_source_kinds=["protocol", "attachment"],
        top_k=8,
        chunk_ids=["chunk-1", "chunk-2"],
    )
    right = build_retrieval_set_id(
        normalized_query="מה אושר",
        requested_source_kinds=["protocol", "attachment"],
        top_k=8,
        chunk_ids=["chunk-1", "chunk-2"],
    )
    changed = build_retrieval_set_id(
        normalized_query="מה אושר",
        requested_source_kinds=["protocol", "attachment"],
        top_k=8,
        chunk_ids=["chunk-1", "chunk-3"],
    )

    assert left == right
    assert left != changed


def test_audit_sample_rate_clamps_and_uses_default() -> None:
    assert audit_sample_rate({}) == DEFAULT_RAG_AUDIT_SAMPLE_RATE
    assert audit_sample_rate({"RAG_AUDIT_SAMPLE_RATE": "0.2"}) == 0.2
    assert audit_sample_rate({"RAG_AUDIT_SAMPLE_RATE": "1.8"}) == 1.0
    assert audit_sample_rate({"RAG_AUDIT_SAMPLE_RATE": "-0.1"}) == 0.0
    assert audit_sample_rate({"RAG_AUDIT_SAMPLE_RATE": "bad"}) == DEFAULT_RAG_AUDIT_SAMPLE_RATE


def test_should_sample_audit_and_hash_text_behave_deterministically() -> None:
    assert should_sample_audit(rate=0.5, random_value=0.2)
    assert not should_sample_audit(rate=0.5, random_value=0.8)
    assert not should_sample_audit(rate=0.0, random_value=0.0)

    digest = hash_text("מה אושר בעיר")
    assert len(digest) == 24
    assert digest == hash_text("מה אושר בעיר")
