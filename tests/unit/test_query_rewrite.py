from __future__ import annotations

from municipality.query_rewrite import QueryRewriteService


def test_query_rewrite_prefers_header_strategy_for_broad_protocol_queries() -> None:
    service = QueryRewriteService()

    result = service.rewrite_for_retrieval(query="באיזה פרוטוקול דנו בחניה ליד בית הספר?")

    assert result.retrieval_strategy == "headers"
    assert result.use_neighbors is True
    assert result.artifact_kind_priority[0] in {"document_profile", "header_anchor"}


def test_query_rewrite_prefers_segment_strategy_for_focused_decisions() -> None:
    service = QueryRewriteService()

    result = service.rewrite_for_retrieval(query="מה הוחלט לגבי הרחבת החניה ליד בית הספר?")

    assert result.retrieval_strategy == "segments"
    assert result.use_neighbors is True
    assert "מה הוחלט לגבי הרחבת החניה ליד בית הספר?" in result.lexical_terms
