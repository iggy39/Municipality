from __future__ import annotations

from municipality.semantic_canonicalization import SemanticCanonicalizer


def test_normalize_text_applies_nfkc_and_cleanup_rules() -> None:
    canonicalizer = SemanticCanonicalizer()
    raw = "  \u202b'ועדת'  תחבורה\u200f--רח'  \u202c  "

    normalized = canonicalizer.normalize_text(raw)

    assert normalized == "ועדת תחבורה-רחוב"
