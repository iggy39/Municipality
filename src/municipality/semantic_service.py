from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from municipality.semantic_canonicalization import CanonicalizationReport, SemanticCanonicalizer
from municipality.semantic_extractor import SemanticExtractor
from municipality.semantic_prompt import (
    SemanticEvidencePacket,
    build_semantic_evidence_packet,
    build_semantic_model_request,
    prompt_hash_for_payload,
)


@dataclass(slots=True)
class SemanticServiceResult:
    run_id: int | None
    status: str
    from_cache: bool
    api_call_count: int
    evidence_spans: int
    node_candidates: int
    accepted_nodes: int
    rejected_nodes: int
    validation_issues: int


class SemanticService:
    def __init__(
        self,
        session: Session,
        *,
        extractor: SemanticExtractor | None = None,
        canonicalizer: SemanticCanonicalizer | None = None,
    ):
        self.session = session
        self.extractor = extractor or SemanticExtractor(session)
        self.canonicalizer = canonicalizer or SemanticCanonicalizer()

    def run_for_document(
        self,
        *,
        source_site_id: int,
        document_version_id: int,
        source_kind: str,
        extracted_text: str,
        citation_map: list[dict[str, int]],
    ) -> SemanticServiceResult:
        evidence_packet = build_semantic_evidence_packet(
            extracted_text=extracted_text,
            citation_map=citation_map,
        )
        request_payload = build_semantic_model_request(
            document_version_id=document_version_id,
            source_kind=source_kind,
            evidence_packet=evidence_packet,
        )
        request_hash = prompt_hash_for_payload(request_payload)

        extraction_result = self.extractor.extract_once(
            document_version_id=document_version_id,
            prompt_hash=request_hash,
            request_payload=request_payload,
        )

        output = extraction_result.output
        canonical_report = CanonicalizationReport()
        if output is not None:
            canonical_report = self.canonicalizer.canonicalize_candidates(
                source_site_id=source_site_id,
                nodes=output.nodes,
                extracted_text=extracted_text,
                citation_map=citation_map,
                evidence_spans=output.evidence_spans,
            )
            extraction_result.run.canonicalization_report_json = json.dumps(
                _canonicalization_report_to_dict(canonical_report),
                ensure_ascii=False,
            )
            self.session.flush()

        issue_count = len(extraction_result.validation_report.issues) if extraction_result.validation_report else 0
        return SemanticServiceResult(
            run_id=extraction_result.run.id,
            status=extraction_result.run.status,
            from_cache=extraction_result.from_cache,
            api_call_count=extraction_result.run.api_call_count,
            evidence_spans=len(output.evidence_spans) if output is not None else 0,
            node_candidates=len(output.nodes) if output is not None else 0,
            accepted_nodes=len(canonical_report.accepted_nodes),
            rejected_nodes=len(canonical_report.rejected_nodes),
            validation_issues=issue_count,
        )

    def build_evidence_packet(
        self,
        *,
        extracted_text: str,
        citation_map: list[dict[str, int]],
    ) -> SemanticEvidencePacket:
        return build_semantic_evidence_packet(
            extracted_text=extracted_text,
            citation_map=citation_map,
        )


def _canonicalization_report_to_dict(report: CanonicalizationReport) -> dict:
    return {
        "accepted_nodes": [
            {
                "candidate_id": row.candidate_id,
                "label_he": row.label_he,
                "label_norm": row.label_norm,
                "node_kind": row.node_kind,
                "semantic_type": row.semantic_type,
                "parent_candidate_id": row.parent_candidate_id,
                "depth": row.depth,
                "specificity_score": row.specificity_score,
                "confidence": row.confidence,
                "support_count": row.support_count,
                "status": row.status,
                "node_key_hash": row.node_key_hash,
                "reject_reason": row.reject_reason.value if row.reject_reason else None,
            }
            for row in report.accepted_nodes
        ],
        "rejected_nodes": [
            {
                "candidate_id": row.candidate_id,
                "label_he": row.label_he,
                "label_norm": row.label_norm,
                "node_kind": row.node_kind,
                "semantic_type": row.semantic_type,
                "parent_candidate_id": row.parent_candidate_id,
                "depth": row.depth,
                "specificity_score": row.specificity_score,
                "confidence": row.confidence,
                "support_count": row.support_count,
                "status": row.status,
                "node_key_hash": row.node_key_hash,
                "reject_reason": row.reject_reason.value if row.reject_reason else None,
            }
            for row in report.rejected_nodes
        ],
        "warnings": report.warnings,
    }
