from __future__ import annotations

from municipality.semantic_canonicalization import CanonicalizationConfig, SemanticCanonicalizer
from municipality.semantic_contract import (
    SemanticEvidenceCategory,
    SemanticEvidenceSpanCandidate,
    SemanticMentionCandidate,
    SemanticNodeCandidate,
    SemanticNodeKind,
    SemanticRejectReason,
)


def test_reject_reason_flags_generic_topic_label() -> None:
    canonicalizer = SemanticCanonicalizer()

    reason = canonicalizer.reject_reason_for_label(
        label_norm=canonicalizer.normalize_text("כללי"),
        node_kind=SemanticNodeKind.TOPIC,
        parent_label_norm=None,
    )

    assert reason == SemanticRejectReason.TOO_GENERIC


def test_specificity_score_increases_with_support_and_information_gain() -> None:
    canonicalizer = SemanticCanonicalizer()

    low = canonicalizer.compute_specificity_score(
        label_norm=canonicalizer.normalize_text("ועדה"),
        support_count=1,
        evidence_span_density=0.01,
        parent_label_norm=canonicalizer.normalize_text("ועדה"),
    )
    high = canonicalizer.compute_specificity_score(
        label_norm=canonicalizer.normalize_text("ועדת תחבורה עירונית"),
        support_count=3,
        evidence_span_density=0.2,
        parent_label_norm=canonicalizer.normalize_text("ועדות"),
    )

    assert high > low


def test_canonicalize_candidates_caps_depth_and_keeps_deterministic_status() -> None:
    config = CanonicalizationConfig(max_depth=2)
    canonicalizer = SemanticCanonicalizer(config=config)
    extracted_text = "ועדת חינוך שכונת המרינה תוכנית אב תחבורה"
    citation_map = [{"start": 0, "end": len(extracted_text), "page": 1}]

    root = SemanticNodeCandidate(
        candidate_id="n0",
        label_he="ועדת חינוך",
        node_kind=SemanticNodeKind.TOPIC,
        semantic_type="committee",
        confidence=0.9,
        mentions=[_mention_for(extracted_text, "ועדת חינוך")],
    )
    child = SemanticNodeCandidate(
        candidate_id="n1",
        label_he="שכונת המרינה",
        node_kind=SemanticNodeKind.ENTITY,
        semantic_type="neighborhood",
        parent_candidate_id="n0",
        confidence=0.88,
        mentions=[_mention_for(extracted_text, "שכונת המרינה")],
    )
    grandchild = SemanticNodeCandidate(
        candidate_id="n2",
        label_he="תוכנית אב תחבורה",
        node_kind=SemanticNodeKind.TOPIC,
        semantic_type="program",
        parent_candidate_id="n1",
        confidence=0.87,
        mentions=[_mention_for(extracted_text, "תוכנית אב תחבורה")],
    )

    report = canonicalizer.canonicalize_candidates(
        source_site_id=1,
        nodes=[root, child, grandchild],
        extracted_text=extracted_text,
        citation_map=citation_map,
    )

    assert len(report.accepted_nodes) == 3
    deep = next(node for node in report.accepted_nodes if node.candidate_id == "n2")
    assert deep.depth == 2
    assert deep.status in {"active", "candidate"}


def test_option2_allows_non_decision_span_support_to_activate_node() -> None:
    canonicalizer = SemanticCanonicalizer()
    extracted_text = "הוצגה תוכנית תחבורה עירונית לשכונת המרינה"
    citation_map = [{"start": 0, "end": len(extracted_text), "page": 1}]

    node = SemanticNodeCandidate(
        candidate_id="n-plan-1",
        label_he="תוכנית תחבורה עירונית",
        node_kind=SemanticNodeKind.TOPIC,
        semantic_type="plan",
        confidence=0.53,
        mentions=[_mention_for(extracted_text, "תוכנית תחבורה עירונית")],
        evidence_span_ids=["s-plan-1"],
    )
    span = SemanticEvidenceSpanCandidate(
        span_id="s-plan-1",
        category=SemanticEvidenceCategory.PLAN_PROGRAM,
        start_offset=5,
        end_offset=29,
        text="תוכנית תחבורה עירונית",
        confidence=0.8,
        regex_boost=0.08,
    )

    report = canonicalizer.canonicalize_candidates(
        source_site_id=1,
        nodes=[node],
        extracted_text=extracted_text,
        citation_map=citation_map,
        evidence_spans=[span],
    )

    assert len(report.accepted_nodes) == 1
    assert report.accepted_nodes[0].status == "active"
    assert report.accepted_nodes[0].confidence >= canonicalizer.config.min_confidence_for_acceptance


def test_node_without_evidence_refs_requires_stronger_support_for_active_status() -> None:
    canonicalizer = SemanticCanonicalizer()
    extracted_text = "דיון קצר בנושא שכונתי"
    citation_map = [{"start": 0, "end": len(extracted_text), "page": 1}]

    node = SemanticNodeCandidate(
        candidate_id="n-no-ref",
        label_he="דיון שכונתי",
        node_kind=SemanticNodeKind.TOPIC,
        semantic_type="discussion",
        confidence=0.9,
        mentions=[_mention_for(extracted_text, "דיון")],
        evidence_span_ids=[],
    )

    report = canonicalizer.canonicalize_candidates(
        source_site_id=1,
        nodes=[node],
        extracted_text=extracted_text,
        citation_map=citation_map,
        evidence_spans=[],
    )

    assert len(report.accepted_nodes) == 1
    assert report.accepted_nodes[0].status == "candidate"


def test_missing_model_confidence_uses_derived_evidence_confidence() -> None:
    canonicalizer = SemanticCanonicalizer()
    extracted_text = "הוחלט לאשר תקציב תחבורה עירונית לשנת 2026"
    citation_map = [{"start": 0, "end": len(extracted_text), "page": 1}]

    node = SemanticNodeCandidate(
        candidate_id="n-derived",
        label_he="תחבורה עירונית",
        node_kind=SemanticNodeKind.TOPIC,
        semantic_type="transport_program",
        confidence=None,
        mentions=[
            SemanticMentionCandidate(
                mention_text="תחבורה עירונית",
                start_offset=17,
                end_offset=31,
                confidence=None,
            )
        ],
        evidence_span_ids=["s-decision-1"],
    )
    span = SemanticEvidenceSpanCandidate(
        span_id="s-decision-1",
        category=SemanticEvidenceCategory.DECISION,
        start_offset=0,
        end_offset=31,
        text="הוחלט לאשר תקציב תחבורה עירונית",
        confidence=0.84,
        regex_boost=0.08,
    )

    report = canonicalizer.canonicalize_candidates(
        source_site_id=1,
        nodes=[node],
        extracted_text=extracted_text,
        citation_map=citation_map,
        evidence_spans=[span],
    )

    assert len(report.accepted_nodes) == 1
    accepted = report.accepted_nodes[0]
    assert accepted.confidence_source == "derived_from_evidence"
    assert accepted.confidence > 0.0


def _mention_for(text_value: str, target: str) -> SemanticMentionCandidate:
    start = text_value.index(target)
    end = start + len(target)
    return SemanticMentionCandidate(
        mention_text=target,
        start_offset=start,
        end_offset=end,
        confidence=0.9,
    )
