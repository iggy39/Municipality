from __future__ import annotations

from municipality.semantic_canonicalization import SemanticCanonicalizer


def test_alias_merge_auto_merges_exact_equivalents() -> None:
    canonicalizer = SemanticCanonicalizer()

    decision = canonicalizer.alias_merge_decision(
        left_label="ועדת חינוך",
        right_label="ועדת חינוך",
        left_semantic_type="committee",
        right_semantic_type="committee",
        left_parent_hash="root",
        right_parent_hash="root",
    )

    assert decision.action == "auto_merge"
    assert decision.score >= 0.94


def test_alias_merge_blocks_conflicting_numeric_identifiers() -> None:
    canonicalizer = SemanticCanonicalizer()

    decision = canonicalizer.alias_merge_decision(
        left_label="ועדת תקציב 2024",
        right_label="ועדת תקציב 2025",
        left_semantic_type="committee",
        right_semantic_type="committee",
        left_parent_hash="root",
        right_parent_hash="root",
    )

    assert decision.action == "no_merge"
    assert "numeric_identifier_conflict" in decision.blockers


def test_alias_merge_blocks_incompatible_semantic_type() -> None:
    canonicalizer = SemanticCanonicalizer()

    decision = canonicalizer.alias_merge_decision(
        left_label="מרכז הספורט העירוני",
        right_label="מרכז הספורט העירוני",
        left_semantic_type="program",
        right_semantic_type="organization",
        left_parent_hash="root",
        right_parent_hash="root",
    )

    assert decision.action == "no_merge"
    assert "incompatible_semantic_type" in decision.blockers
