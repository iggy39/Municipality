from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

from municipality.extraction import resolve_pages_for_span
from municipality.semantic_contract import (
    SemanticEvidenceCategory,
    SemanticEvidenceSpanCandidate,
    SemanticNodeCandidate,
    SemanticNodeKind,
    SemanticRejectReason,
)


BIDI_ZERO_WIDTH_RE = re.compile(r"[\u200B-\u200F\u202A-\u202E\u2066-\u2069\uFEFF]")
QUOTE_RE = re.compile(r"[\u2018\u2019\u201B\u2032׳״\u0060\u00B4\u02BC\u02BB\"']")
DASH_RE = re.compile(r"(?:--+|[\u2010-\u2015\u2212]+)")
WHITESPACE_RE = re.compile(r"\s+")
NON_TOKEN_RE = re.compile(r"[^\wא-ת]+")
TRIM_EDGE_RE = re.compile(r"^[^\wא-ת]+|[^\wא-ת]+$")

DEFAULT_STOPWORDS = {
    "של",
    "על",
    "עם",
    "גם",
    "או",
    "אך",
    "כי",
    "זה",
    "זו",
    "אלה",
    "בין",
    "לפי",
    "כל",
    "עוד",
    "בלי",
    "for",
    "and",
    "the",
    "of",
}

DEFAULT_GENERIC_DENYLIST = {
    "general",
    "updates",
    "misc",
    "כללי",
    "עדכונים",
    "שונות",
}

DEFAULT_TOP_LEVEL_TOPIC_WHITELIST = {
    "תקציב",
    "חינוך",
    "תחבורה",
    "רווחה",
}

DEFAULT_ABBREVIATION_MAP = {
    "רח'": "רחוב",
    "רח.": "רחוב",
    "רח": "רחוב",
    "שכ'": "שכונה",
    "שכ.": "שכונה",
    "שכ": "שכונה",
    "עיר'": "עיריה",
    "עיר": "עיריה",
    "וע'": "ועדה",
    "וע": "ועדה",
    "אג'": "אגף",
    "אג": "אגף",
}


@dataclass(slots=True)
class CanonicalizationConfig:
    max_depth: int = 6
    threshold_active: float = 0.62
    public_threshold: float = 0.75
    min_mentions_for_acceptance: int = 1
    min_confidence_for_acceptance: float = 0.55
    decision_span_threshold: float = 0.65
    contextual_span_threshold: float = 0.78
    no_ref_min_mentions_for_active: int = 2
    no_ref_specificity_threshold: float = 0.78
    stopword_ratio_threshold: float = 0.7
    stopwords: set[str] = field(default_factory=lambda: set(DEFAULT_STOPWORDS))
    generic_denylist: set[str] = field(default_factory=lambda: set(DEFAULT_GENERIC_DENYLIST))
    top_level_topic_whitelist: set[str] = field(default_factory=lambda: set(DEFAULT_TOP_LEVEL_TOPIC_WHITELIST))
    abbreviation_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ABBREVIATION_MAP))


@dataclass(slots=True)
class AliasMergeDecision:
    score: float
    action: str
    blockers: list[str]


@dataclass(slots=True)
class SpanValidationResult:
    is_valid: bool
    start_page: int | None
    end_page: int | None
    reason_code: SemanticRejectReason | None
    normalized_match: bool
    page_resolved: bool


@dataclass(slots=True)
class NodeEvidenceSupport:
    has_any_refs: bool
    has_decision_support: bool
    has_context_support: bool
    strongest_decision_score: float
    strongest_context_score: float
    confidence_boost: float
    missing_ref_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CanonicalNodeCandidate:
    candidate_id: str
    label_he: str
    label_norm: str
    node_kind: str
    semantic_type: str
    parent_candidate_id: str | None
    depth: int
    specificity_score: float
    confidence: float
    confidence_source: str
    support_count: int
    status: str
    node_key_hash: str
    reject_reason: SemanticRejectReason | None


@dataclass(slots=True)
class CanonicalizationReport:
    accepted_nodes: list[CanonicalNodeCandidate] = field(default_factory=list)
    rejected_nodes: list[CanonicalNodeCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class SemanticCanonicalizer:
    def __init__(self, config: CanonicalizationConfig | None = None):
        self.config = config or CanonicalizationConfig()

    def normalize_text(self, value: str, *, expand_abbreviations: bool = True) -> str:
        normalized = unicodedata.normalize("NFKC", value)
        normalized = BIDI_ZERO_WIDTH_RE.sub("", normalized)
        normalized = QUOTE_RE.sub("'", normalized)
        normalized = DASH_RE.sub("-", normalized)
        normalized = WHITESPACE_RE.sub(" ", normalized).strip()
        normalized = self._trim_punctuation_tokens(normalized)
        if expand_abbreviations and normalized:
            normalized = self._expand_abbreviations(normalized)
        normalized = WHITESPACE_RE.sub(" ", normalized).strip().casefold()
        return normalized

    def reject_reason_for_label(
        self,
        *,
        label_norm: str,
        node_kind: SemanticNodeKind,
        parent_label_norm: str | None,
    ) -> SemanticRejectReason | None:
        if not label_norm:
            return SemanticRejectReason.EMPTY_LABEL
        if _is_numeric_only(label_norm):
            return SemanticRejectReason.NUMERIC_ONLY_LABEL

        tokens = _tokens(label_norm)
        if not tokens:
            return SemanticRejectReason.EMPTY_LABEL
        if all(token in self.config.stopwords for token in tokens):
            return SemanticRejectReason.STOPWORDS_ONLY

        if label_norm in self.config.generic_denylist:
            return SemanticRejectReason.TOO_GENERIC

        informative_tokens = [token for token in tokens if token not in self.config.stopwords]
        if node_kind == SemanticNodeKind.TOPIC and len(informative_tokens) < 2:
            if label_norm not in self.config.top_level_topic_whitelist:
                return SemanticRejectReason.TOO_GENERIC

        stopword_ratio = (len(tokens) - len(informative_tokens)) / max(1, len(tokens))
        if stopword_ratio > self.config.stopword_ratio_threshold:
            return SemanticRejectReason.TOO_GENERIC

        if parent_label_norm:
            parent_tokens = set(_tokens(parent_label_norm))
            if parent_tokens and set(tokens).issubset(parent_tokens):
                return SemanticRejectReason.TOO_GENERIC

        return None

    def compute_specificity_score(
        self,
        *,
        label_norm: str,
        support_count: int,
        evidence_span_density: float,
        parent_label_norm: str | None,
    ) -> float:
        label_tokens = _tokens(label_norm)
        informative_tokens = [token for token in label_tokens if token not in self.config.stopwords]

        lexical_distinctiveness = len(set(informative_tokens)) / max(1, len(label_tokens))
        mention_support = min(1.0, support_count / 3.0)
        span_density = max(0.0, min(1.0, evidence_span_density))
        parent_gain = self._parent_information_gain(
            tokens=informative_tokens,
            parent_label_norm=parent_label_norm,
        )

        score = (
            0.45 * lexical_distinctiveness
            + 0.25 * mention_support
            + 0.15 * span_density
            + 0.15 * parent_gain
        )
        return round(max(0.0, min(1.0, score)), 6)

    def alias_merge_decision(
        self,
        *,
        left_label: str,
        right_label: str,
        left_semantic_type: str,
        right_semantic_type: str,
        left_parent_hash: str | None,
        right_parent_hash: str | None,
    ) -> AliasMergeDecision:
        blockers: list[str] = []
        if left_semantic_type != right_semantic_type:
            blockers.append("incompatible_semantic_type")
        if left_parent_hash and right_parent_hash and left_parent_hash != right_parent_hash:
            blockers.append("parent_mismatch")

        left_norm = self.normalize_text(left_label, expand_abbreviations=False)
        right_norm = self.normalize_text(right_label, expand_abbreviations=False)
        if _has_numeric_conflict(left_norm, right_norm):
            blockers.append("numeric_identifier_conflict")

        score = self.similarity_score(left_norm, right_norm)
        if blockers:
            return AliasMergeDecision(score=score, action="no_merge", blockers=blockers)
        if score >= 0.94:
            return AliasMergeDecision(score=score, action="auto_merge", blockers=[])
        if score >= 0.85:
            return AliasMergeDecision(score=score, action="merge_review_needed", blockers=[])
        return AliasMergeDecision(score=score, action="no_merge", blockers=[])

    def similarity_score(self, left_norm: str, right_norm: str) -> float:
        exact_match = 1.0 if left_norm == right_norm else 0.0
        left_tokens = set(_tokens(left_norm))
        right_tokens = set(_tokens(right_norm))
        jaccard = _jaccard(left_tokens, right_tokens)
        trigram = _jaccard(_char_trigrams(left_norm), _char_trigrams(right_norm))

        left_expanded = self.normalize_text(left_norm, expand_abbreviations=True)
        right_expanded = self.normalize_text(right_norm, expand_abbreviations=True)
        abbreviation_equivalence = 1.0 if left_expanded == right_expanded else 0.0
        numeric_consistency = _numeric_consistency(left_norm, right_norm)

        score = (
            0.05 * exact_match
            + 0.40 * jaccard
            + 0.30 * trigram
            + 0.15 * abbreviation_equivalence
            + 0.10 * numeric_consistency
        )
        return round(max(0.0, min(1.0, score)), 6)

    def node_key_hash(
        self,
        *,
        source_site_id: int,
        node_kind: str,
        semantic_type: str,
        parent_node_key_hash_or_root: str,
        pref_label_norm: str,
    ) -> str:
        canonical_key = (
            f"v1|{source_site_id}|{node_kind}|{semantic_type}|{parent_node_key_hash_or_root}|{pref_label_norm}"
        )
        return hashlib.sha1(canonical_key.encode("utf-8")).hexdigest()[:40]

    def alias_hash(self, *, semantic_node_id: int, alias_label_norm: str) -> str:
        canonical_key = f"v1|{semantic_node_id}|{alias_label_norm}"
        return hashlib.sha1(canonical_key.encode("utf-8")).hexdigest()[:40]

    def mention_evidence_hash(
        self,
        *,
        document_version_id: int,
        semantic_node_id: int,
        start_offset: int,
        end_offset: int,
        mention_text_norm: str,
    ) -> str:
        canonical_key = (
            f"v1|{document_version_id}|{semantic_node_id}|{start_offset}|{end_offset}|{mention_text_norm}"
        )
        return hashlib.sha1(canonical_key.encode("utf-8")).hexdigest()[:40]

    def validate_mention_span(
        self,
        *,
        extracted_text: str,
        citation_map: list[dict[str, int]],
        mention_text: str,
        start_offset: int,
        end_offset: int,
        allow_unresolved_page: bool = True,
    ) -> SpanValidationResult:
        if not (0 <= start_offset < end_offset <= len(extracted_text)):
            return SpanValidationResult(
                is_valid=False,
                start_page=None,
                end_page=None,
                reason_code=SemanticRejectReason.SPAN_OUT_OF_RANGE,
                normalized_match=False,
                page_resolved=False,
            )

        source_span = extracted_text[start_offset:end_offset]
        normalized_span = self.normalize_text(source_span)
        normalized_mention = self.normalize_text(mention_text)
        strict_match = mention_text in source_span
        normalized_match = bool(normalized_mention) and (
            normalized_mention in normalized_span or normalized_span in normalized_mention
        )
        if not strict_match and not normalized_match:
            return SpanValidationResult(
                is_valid=False,
                start_page=None,
                end_page=None,
                reason_code=SemanticRejectReason.SPAN_TEXT_MISMATCH,
                normalized_match=False,
                page_resolved=False,
            )

        pages = resolve_pages_for_span(citation_map, start_offset, end_offset)
        if not pages and not allow_unresolved_page:
            return SpanValidationResult(
                is_valid=False,
                start_page=None,
                end_page=None,
                reason_code=SemanticRejectReason.PAGE_RESOLUTION_FAILED,
                normalized_match=normalized_match,
                page_resolved=False,
            )

        return SpanValidationResult(
            is_valid=True,
            start_page=min(pages) if pages else None,
            end_page=max(pages) if pages else None,
            reason_code=None,
            normalized_match=normalized_match,
            page_resolved=bool(pages),
        )

    def canonicalize_candidates(
        self,
        *,
        source_site_id: int,
        nodes: list[SemanticNodeCandidate],
        extracted_text: str,
        citation_map: list[dict[str, int]],
        evidence_spans: list[SemanticEvidenceSpanCandidate] | None = None,
    ) -> CanonicalizationReport:
        by_id = {node.candidate_id: node for node in nodes}
        report = CanonicalizationReport()
        evidence_by_id = {span.span_id: span for span in (evidence_spans or [])}

        depth_cache: dict[str, int] = {}
        node_hash_by_candidate_id: dict[str, str] = {}

        ordered = sorted(nodes, key=lambda item: (item.parent_candidate_id or "", item.candidate_id))
        for node in ordered:
            depth, has_cycle = self._resolve_depth(node, by_id, depth_cache)
            label_norm = self.normalize_text(node.label_he)
            parent_label_norm = None
            parent_hash = "ROOT"
            if node.parent_candidate_id and node.parent_candidate_id in by_id:
                parent = by_id[node.parent_candidate_id]
                parent_label_norm = self.normalize_text(parent.label_he)
                parent_hash = node_hash_by_candidate_id.get(node.parent_candidate_id, "ROOT")

            reject_reason = None
            if has_cycle:
                reject_reason = SemanticRejectReason.HIERARCHY_INTEGRITY
            else:
                reject_reason = self.reject_reason_for_label(
                    label_norm=label_norm,
                    node_kind=node.node_kind,
                    parent_label_norm=parent_label_norm,
                )

            valid_mentions = 0
            total_mention_chars = 0
            if reject_reason is None:
                for mention in node.mentions:
                    validation = self.validate_mention_span(
                        extracted_text=extracted_text,
                        citation_map=citation_map,
                        mention_text=mention.mention_text,
                        start_offset=mention.start_offset,
                        end_offset=mention.end_offset,
                    )
                    if validation.is_valid:
                        valid_mentions += 1
                        total_mention_chars += max(0, mention.end_offset - mention.start_offset)

            if reject_reason is None and valid_mentions == 0:
                reject_reason = SemanticRejectReason.SPAN_TEXT_MISMATCH

            evidence_density = total_mention_chars / max(1, len(extracted_text))
            specificity = self.compute_specificity_score(
                label_norm=label_norm,
                support_count=valid_mentions,
                evidence_span_density=evidence_density,
                parent_label_norm=parent_label_norm,
            )

            node_evidence_support = self._node_evidence_support(node=node, evidence_by_id=evidence_by_id)
            if node_evidence_support.missing_ref_ids:
                report.warnings.append(
                    f"node={node.candidate_id}: missing evidence refs [{', '.join(node_evidence_support.missing_ref_ids)}]"
                )

            base_confidence, confidence_source = self._resolve_node_confidence(
                node=node,
                support_count=valid_mentions,
                specificity=specificity,
                node_evidence_support=node_evidence_support,
            )
            adjusted_confidence = min(1.0, base_confidence + node_evidence_support.confidence_boost)
            if node_evidence_support.has_any_refs:
                category_gate_ok = node_evidence_support.has_decision_support or node_evidence_support.has_context_support
            else:
                category_gate_ok = (
                    valid_mentions >= self.config.no_ref_min_mentions_for_active
                    and specificity >= self.config.no_ref_specificity_threshold
                )

            clamped_depth = min(depth, self.config.max_depth)
            status = "candidate"
            if reject_reason is None:
                if (
                    specificity >= self.config.threshold_active
                    and valid_mentions >= self.config.min_mentions_for_acceptance
                    and adjusted_confidence >= self.config.min_confidence_for_acceptance
                    and category_gate_ok
                ):
                    status = "active"

            key_hash = self.node_key_hash(
                source_site_id=source_site_id,
                node_kind=node.node_kind.value,
                semantic_type=node.semantic_type,
                parent_node_key_hash_or_root=parent_hash,
                pref_label_norm=label_norm,
            )
            node_hash_by_candidate_id[node.candidate_id] = key_hash

            normalized = CanonicalNodeCandidate(
                candidate_id=node.candidate_id,
                label_he=node.label_he,
                label_norm=label_norm,
                node_kind=node.node_kind.value,
                semantic_type=node.semantic_type,
                parent_candidate_id=node.parent_candidate_id,
                depth=clamped_depth,
                specificity_score=specificity,
                confidence=adjusted_confidence,
                confidence_source=confidence_source,
                support_count=valid_mentions,
                status=status,
                node_key_hash=key_hash,
                reject_reason=reject_reason,
            )

            if reject_reason is None:
                report.accepted_nodes.append(normalized)
            else:
                report.rejected_nodes.append(normalized)

        return report

    def _resolve_node_confidence(
        self,
        *,
        node: SemanticNodeCandidate,
        support_count: int,
        specificity: float,
        node_evidence_support: NodeEvidenceSupport,
    ) -> tuple[float, str]:
        model_confidence = _normalize_optional_confidence(node.confidence)
        if model_confidence is not None:
            return model_confidence, "model"

        evidence_strength = max(
            node_evidence_support.strongest_decision_score,
            node_evidence_support.strongest_context_score,
        )
        support_factor = min(1.0, support_count / 3.0)
        derived_confidence = (
            0.05
            + (0.45 * evidence_strength)
            + (0.25 * specificity)
            + (0.25 * support_factor)
        )

        # Keep derived confidence conservative when model omitted confidence.
        derived_confidence = min(0.75, derived_confidence)
        return round(max(0.0, min(1.0, derived_confidence)), 6), "derived_from_evidence"

    def _node_evidence_support(
        self,
        *,
        node: SemanticNodeCandidate,
        evidence_by_id: dict[str, SemanticEvidenceSpanCandidate],
    ) -> NodeEvidenceSupport:
        if not node.evidence_span_ids:
            return NodeEvidenceSupport(
                has_any_refs=False,
                has_decision_support=False,
                has_context_support=False,
                strongest_decision_score=0.0,
                strongest_context_score=0.0,
                confidence_boost=0.0,
            )

        strongest_decision_score = 0.0
        strongest_context_score = 0.0
        missing_ref_ids: list[str] = []

        for span_id in node.evidence_span_ids:
            span = evidence_by_id.get(span_id)
            if span is None:
                missing_ref_ids.append(span_id)
                continue
            effective_score = min(1.0, span.confidence + max(0.0, min(0.12, span.regex_boost)))
            if span.category == SemanticEvidenceCategory.DECISION:
                strongest_decision_score = max(strongest_decision_score, effective_score)
            else:
                strongest_context_score = max(strongest_context_score, effective_score)

        has_decision_support = strongest_decision_score >= self.config.decision_span_threshold
        has_context_support = strongest_context_score >= self.config.contextual_span_threshold

        confidence_boost = 0.0
        if has_decision_support:
            confidence_boost += 0.05
        if has_context_support:
            confidence_boost += 0.04

        return NodeEvidenceSupport(
            has_any_refs=True,
            has_decision_support=has_decision_support,
            has_context_support=has_context_support,
            strongest_decision_score=strongest_decision_score,
            strongest_context_score=strongest_context_score,
            confidence_boost=confidence_boost,
            missing_ref_ids=missing_ref_ids,
        )

    def _resolve_depth(
        self,
        node: SemanticNodeCandidate,
        by_id: dict[str, SemanticNodeCandidate],
        depth_cache: dict[str, int],
    ) -> tuple[int, bool]:
        if node.candidate_id in depth_cache:
            return depth_cache[node.candidate_id], False

        seen: set[str] = set()
        depth = 0
        current = node
        while current.parent_candidate_id:
            parent_id = current.parent_candidate_id
            if parent_id in seen:
                return self.config.max_depth, True
            seen.add(parent_id)
            parent = by_id.get(parent_id)
            if parent is None:
                break
            depth += 1
            if depth >= self.config.max_depth:
                break
            current = parent

        depth_cache[node.candidate_id] = depth
        return depth, False

    def _trim_punctuation_tokens(self, value: str) -> str:
        tokens = [TRIM_EDGE_RE.sub("", token) for token in value.split(" ") if token]
        tokens = [token for token in tokens if token]
        while tokens and NON_TOKEN_RE.sub("", tokens[0]) == "":
            tokens.pop(0)
        while tokens and NON_TOKEN_RE.sub("", tokens[-1]) == "":
            tokens.pop()
        return " ".join(tokens).strip()

    def _expand_abbreviations(self, value: str) -> str:
        tokens = [token for token in value.split(" ") if token]
        expanded: list[str] = []
        for token in tokens:
            if "-" not in token:
                expanded.append(self.config.abbreviation_map.get(token, token))
                continue
            parts = [self.config.abbreviation_map.get(part, part) for part in token.split("-")]
            expanded.append("-".join(parts))
        return " ".join(expanded)

    def _parent_information_gain(self, *, tokens: list[str], parent_label_norm: str | None) -> float:
        if not tokens:
            return 0.0
        if not parent_label_norm:
            return 1.0
        parent_tokens = set(_tokens(parent_label_norm))
        if not parent_tokens:
            return 1.0
        new_tokens = [token for token in tokens if token not in parent_tokens]
        return len(new_tokens) / max(1, len(tokens))


def _tokens(value: str) -> list[str]:
    compact = NON_TOKEN_RE.sub(" ", value)
    return [token for token in compact.split(" ") if token]


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 1.0
    if not left_set or not right_set:
        return 0.0
    return len(left_set.intersection(right_set)) / len(left_set.union(right_set))


def _normalize_optional_confidence(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(max(0.0, min(1.0, float(value))), 6)


def _char_trigrams(value: str) -> set[str]:
    if len(value) < 3:
        return {value} if value else set()
    return {value[idx : idx + 3] for idx in range(len(value) - 2)}


def _numeric_tokens(value: str) -> set[str]:
    return set(re.findall(r"\d+", value))


def _numeric_consistency(left_norm: str, right_norm: str) -> float:
    left_numbers = _numeric_tokens(left_norm)
    right_numbers = _numeric_tokens(right_norm)
    if not left_numbers and not right_numbers:
        return 1.0
    if left_numbers == right_numbers:
        return 1.0
    if left_numbers and right_numbers and left_numbers != right_numbers:
        return 0.0
    return 0.5


def _has_numeric_conflict(left_norm: str, right_norm: str) -> bool:
    left_numbers = _numeric_tokens(left_norm)
    right_numbers = _numeric_tokens(right_norm)
    return bool(left_numbers and right_numbers and left_numbers != right_numbers)


def _is_numeric_only(value: str) -> bool:
    stripped = value.replace(" ", "")
    return bool(stripped) and stripped.isdigit()
