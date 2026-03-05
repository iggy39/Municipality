from __future__ import annotations

from municipality.semantic_canonicalization import SemanticCanonicalizer


def test_node_key_hash_is_stable_and_parent_sensitive() -> None:
    canonicalizer = SemanticCanonicalizer()

    root_hash = canonicalizer.node_key_hash(
        source_site_id=1,
        node_kind="topic",
        semantic_type="committee",
        parent_node_key_hash_or_root="ROOT",
        pref_label_norm="ועדת חינוך",
    )
    root_hash_again = canonicalizer.node_key_hash(
        source_site_id=1,
        node_kind="topic",
        semantic_type="committee",
        parent_node_key_hash_or_root="ROOT",
        pref_label_norm="ועדת חינוך",
    )
    child_hash = canonicalizer.node_key_hash(
        source_site_id=1,
        node_kind="topic",
        semantic_type="committee",
        parent_node_key_hash_or_root="PARENT",
        pref_label_norm="ועדת חינוך",
    )

    assert root_hash == root_hash_again
    assert len(root_hash) == 40
    assert child_hash != root_hash


def test_alias_and_mention_hashes_are_deterministic() -> None:
    canonicalizer = SemanticCanonicalizer()

    alias_hash = canonicalizer.alias_hash(semantic_node_id=77, alias_label_norm="רחוב הרצל")
    alias_hash_again = canonicalizer.alias_hash(semantic_node_id=77, alias_label_norm="רחוב הרצל")
    evidence_hash = canonicalizer.mention_evidence_hash(
        document_version_id=12,
        semantic_node_id=77,
        start_offset=10,
        end_offset=22,
        mention_text_norm="רחוב הרצל",
    )

    assert alias_hash == alias_hash_again
    assert len(alias_hash) == 40
    assert len(evidence_hash) == 40
    assert alias_hash != evidence_hash
