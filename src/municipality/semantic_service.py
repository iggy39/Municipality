from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from municipality.semantic_canonicalization import CanonicalizationReport, SemanticCanonicalizer
from municipality.models import (
    ArtifactSemanticLink,
    Decision,
    DecisionSemanticLink,
    RetrievalArtifact,
    SemanticAlias,
    SemanticCandidateReject,
    SemanticDocumentRun,
    SemanticEdge,
    SemanticMention,
    SemanticNode,
)
from municipality.semantic_contract import SemanticExtractionOutput, SemanticRejectReason, SemanticValidationReport
from municipality.semantic_contract import parse_semantic_model_output
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
    aliases: int = 0
    mentions: int = 0
    edges: int = 0
    decision_links: int = 0
    artifact_links: int = 0
    reject_rows: int = 0


@dataclass(slots=True)
class SemanticPersistenceStats:
    aliases: int = 0
    mentions: int = 0
    edges: int = 0
    decision_links: int = 0
    artifact_links: int = 0
    reject_rows: int = 0


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
        document_id: int,
        document_version_id: int,
        source_kind: str,
        extracted_text: str,
        citation_map: list[dict[str, int]],
    ) -> SemanticServiceResult:
        return self._run_external_document(
            source_site_id=source_site_id,
            document_id=document_id,
            document_version_id=document_version_id,
            source_kind=source_kind,
            extracted_text=extracted_text,
            citation_map=citation_map,
        )

    def rebuild_from_recorded_run(
        self,
        *,
        source_site_id: int,
        document_id: int,
        document_version_id: int,
        source_kind: str,
        extracted_text: str,
        citation_map: list[dict[str, int]],
    ) -> SemanticServiceResult | None:
        run = self.session.execute(
            select(SemanticDocumentRun)
            .where(SemanticDocumentRun.document_version_id == document_version_id)
            .where(SemanticDocumentRun.status == "completed")
            .where(SemanticDocumentRun.extraction_payload_json.is_not(None))
            .order_by(SemanticDocumentRun.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if run is None:
            return None

        try:
            payload = json.loads(run.extraction_payload_json or "{}")
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            return None

        output, validation_report = parse_semantic_model_output(payload)
        canonical_report = CanonicalizationReport()
        persistence_stats = SemanticPersistenceStats()
        can_persist_nodes = bool(validation_report.is_valid)

        if can_persist_nodes:
            canonical_report = self.canonicalizer.canonicalize_candidates(
                source_site_id=source_site_id,
                nodes=output.nodes,
                extracted_text=extracted_text,
                citation_map=citation_map,
                evidence_spans=output.evidence_spans,
            )
            persistence_stats = self._persist_run_artifacts(
                run_id=run.id,
                source_site_id=source_site_id,
                document_id=document_id,
                document_version_id=document_version_id,
                source_kind=source_kind,
                output=output,
                canonical_report=canonical_report,
                extracted_text=extracted_text,
                citation_map=citation_map,
                validation_report=validation_report,
            )
            run.canonicalization_report_json = json.dumps(
                _canonicalization_report_to_dict(canonical_report),
                ensure_ascii=False,
            )
            self.session.flush()
        else:
            persistence_stats.reject_rows = self._replace_reject_rows(
                run_id=run.id,
                canonical_report=canonical_report,
                output=output,
                validation_report=validation_report,
            )
            run.canonicalization_report_json = json.dumps(
                _canonicalization_report_to_dict(canonical_report),
                ensure_ascii=False,
            )
            self.session.flush()

        return SemanticServiceResult(
            run_id=run.id,
            status=run.status,
            from_cache=True,
            api_call_count=0,
            evidence_spans=len(output.evidence_spans),
            node_candidates=len(output.nodes),
            accepted_nodes=len(canonical_report.accepted_nodes),
            rejected_nodes=len(canonical_report.rejected_nodes),
            validation_issues=len(validation_report.issues),
            aliases=persistence_stats.aliases,
            mentions=persistence_stats.mentions,
            edges=persistence_stats.edges,
            decision_links=persistence_stats.decision_links,
            artifact_links=persistence_stats.artifact_links,
            reject_rows=persistence_stats.reject_rows,
        )

    def _run_external_document(
        self,
        *,
        source_site_id: int,
        document_id: int,
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
        persistence_stats = SemanticPersistenceStats()
        validation_report = extraction_result.validation_report
        can_persist_nodes = output is not None and bool(validation_report and validation_report.is_valid)

        if can_persist_nodes and output is not None:
            canonical_report = self.canonicalizer.canonicalize_candidates(
                source_site_id=source_site_id,
                nodes=output.nodes,
                extracted_text=extracted_text,
                citation_map=citation_map,
                evidence_spans=output.evidence_spans,
            )
            persistence_stats = self._persist_run_artifacts(
                run_id=extraction_result.run.id,
                source_site_id=source_site_id,
                document_id=document_id,
                document_version_id=document_version_id,
                source_kind=source_kind,
                output=output,
                canonical_report=canonical_report,
                extracted_text=extracted_text,
                citation_map=citation_map,
                validation_report=validation_report,
            )
            extraction_result.run.canonicalization_report_json = json.dumps(
                _canonicalization_report_to_dict(canonical_report),
                ensure_ascii=False,
            )
            self.session.flush()
        else:
            persistence_stats.reject_rows = self._replace_reject_rows(
                run_id=extraction_result.run.id,
                canonical_report=canonical_report,
                output=output,
                validation_report=validation_report,
            )
            extraction_result.run.canonicalization_report_json = json.dumps(
                _canonicalization_report_to_dict(canonical_report),
                ensure_ascii=False,
            )
            self.session.flush()

        issue_count = len(validation_report.issues) if validation_report else 0
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
            aliases=persistence_stats.aliases,
            mentions=persistence_stats.mentions,
            edges=persistence_stats.edges,
            decision_links=persistence_stats.decision_links,
            artifact_links=persistence_stats.artifact_links,
            reject_rows=persistence_stats.reject_rows,
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

    def _persist_run_artifacts(
        self,
        *,
        run_id: int,
        source_site_id: int,
        document_id: int,
        document_version_id: int,
        source_kind: str,
        output: SemanticExtractionOutput,
        canonical_report: CanonicalizationReport,
        extracted_text: str,
        citation_map: list[dict[str, int]],
        validation_report: SemanticValidationReport | None,
    ) -> SemanticPersistenceStats:
        stats = SemanticPersistenceStats()
        accepted_by_id = {row.candidate_id: row for row in canonical_report.accepted_nodes}
        source_nodes_by_id = {node.candidate_id: node for node in output.nodes}

        candidate_to_node = self._upsert_semantic_nodes(
            source_site_id=source_site_id,
            document_version_id=document_version_id,
            accepted_by_id=accepted_by_id,
            source_nodes_by_id=source_nodes_by_id,
        )

        stats.aliases = self._upsert_aliases(
            run_id=run_id,
            document_version_id=document_version_id,
            source_nodes_by_id=source_nodes_by_id,
            candidate_to_node=candidate_to_node,
        )

        mention_index_map, mention_rows_by_candidate = self._upsert_mentions(
            run_id=run_id,
            document_id=document_id,
            document_version_id=document_version_id,
            source_kind=source_kind,
            extracted_text=extracted_text,
            citation_map=citation_map,
            source_nodes_by_id=source_nodes_by_id,
            candidate_to_node=candidate_to_node,
        )
        stats.mentions = sum(len(rows) for rows in mention_rows_by_candidate.values())

        stats.edges = self._upsert_edges(
            run_id=run_id,
            output=output,
            candidate_to_node=candidate_to_node,
        )

        stats.decision_links = self._upsert_decision_links(
            run_id=run_id,
            output=output,
            candidate_to_node=candidate_to_node,
            mention_index_map=mention_index_map,
        )

        stats.artifact_links = self._upsert_artifact_links(
            run_id=run_id,
            document_version_id=document_version_id,
            candidate_to_node=candidate_to_node,
            mention_rows_by_candidate=mention_rows_by_candidate,
        )

        stats.reject_rows = self._replace_reject_rows(
            run_id=run_id,
            canonical_report=canonical_report,
            output=output,
            validation_report=validation_report,
        )
        self.session.flush()
        return stats

    def _upsert_semantic_nodes(
        self,
        *,
        source_site_id: int,
        document_version_id: int,
        accepted_by_id: dict[str, Any],
        source_nodes_by_id: dict[str, Any],
    ) -> dict[str, SemanticNode]:
        if not accepted_by_id:
            return {}

        hashes = [item.node_key_hash for item in accepted_by_id.values()]
        existing_rows = self.session.execute(
            select(SemanticNode).where(
                SemanticNode.source_site_id == source_site_id,
                SemanticNode.node_key_hash.in_(hashes),
            )
        ).scalars().all()
        existing_by_hash = {row.node_key_hash: row for row in existing_rows}

        candidate_to_node: dict[str, SemanticNode] = {}
        ordered = sorted(accepted_by_id.values(), key=lambda row: (row.depth, row.candidate_id))
        now = datetime.utcnow()

        for accepted in ordered:
            parent_node_id = None
            if accepted.parent_candidate_id:
                parent = candidate_to_node.get(accepted.parent_candidate_id)
                if parent is not None:
                    parent_node_id = parent.id

            row = existing_by_hash.get(accepted.node_key_hash)
            if row is None:
                row = SemanticNode(
                    source_site_id=source_site_id,
                    node_key_hash=accepted.node_key_hash,
                    node_kind=accepted.node_kind,
                    semantic_type=accepted.semantic_type,
                    pref_label_he=accepted.label_he,
                    pref_label_norm=accepted.label_norm,
                    parent_node_id=parent_node_id,
                    depth=accepted.depth,
                    specificity_score=accepted.specificity_score,
                    confidence=accepted.confidence,
                    support_count=accepted.support_count,
                    status=accepted.status,
                    first_seen_document_version_id=document_version_id,
                    last_seen_document_version_id=document_version_id,
                    metadata_json=None,
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(row)
                self.session.flush()
                existing_by_hash[accepted.node_key_hash] = row

            source_node = source_nodes_by_id.get(accepted.candidate_id)
            metadata = {
                "candidate_id": accepted.candidate_id,
                "support_count": accepted.support_count,
                "confidence_source": accepted.confidence_source,
            }
            if source_node is not None and source_node.evidence_span_ids:
                metadata["evidence_span_ids"] = list(source_node.evidence_span_ids)

            row.node_kind = accepted.node_kind
            row.semantic_type = accepted.semantic_type
            row.pref_label_he = accepted.label_he
            row.pref_label_norm = accepted.label_norm
            row.parent_node_id = parent_node_id
            row.depth = accepted.depth
            row.specificity_score = accepted.specificity_score
            row.confidence = accepted.confidence
            row.support_count = accepted.support_count
            row.status = accepted.status
            if row.first_seen_document_version_id is None:
                row.first_seen_document_version_id = document_version_id
            row.last_seen_document_version_id = document_version_id
            row.metadata_json = json.dumps(metadata, ensure_ascii=False)
            row.updated_at = now
            self.session.flush()

            candidate_to_node[accepted.candidate_id] = row

        return candidate_to_node

    def _upsert_aliases(
        self,
        *,
        run_id: int,
        document_version_id: int,
        source_nodes_by_id: dict[str, Any],
        candidate_to_node: dict[str, SemanticNode],
    ) -> int:
        if not candidate_to_node:
            return 0

        node_ids = [row.id for row in candidate_to_node.values()]
        existing = self.session.execute(
            select(SemanticAlias).where(SemanticAlias.semantic_node_id.in_(node_ids))
        ).scalars().all()
        existing_by_key = {(row.semantic_node_id, row.alias_hash): row for row in existing}

        touched: set[tuple[int, str]] = set()
        for candidate_id, node_row in candidate_to_node.items():
            source_node = source_nodes_by_id.get(candidate_id)
            if source_node is None:
                continue

            alias_specs = [(source_node.label_he, "surface", source_node.confidence)]
            alias_specs.extend(
                (alias.alias_label_he, alias.alias_kind or "surface", alias.confidence)
                for alias in source_node.aliases
            )

            seen_norms: set[str] = set()
            for alias_label_he, alias_kind, alias_confidence in alias_specs:
                alias_norm = self.canonicalizer.normalize_text(alias_label_he)
                if not alias_norm or alias_norm in seen_norms:
                    continue
                seen_norms.add(alias_norm)

                alias_hash = self.canonicalizer.alias_hash(
                    semantic_node_id=node_row.id,
                    alias_label_norm=alias_norm,
                )
                row = existing_by_key.get((node_row.id, alias_hash))
                if row is None:
                    row = SemanticAlias(
                        semantic_node_id=node_row.id,
                        alias_hash=alias_hash,
                        alias_label_he=alias_label_he,
                        alias_label_norm=alias_norm,
                        alias_kind=alias_kind,
                        confidence=_clamp_score(alias_confidence),
                        first_seen_document_version_id=document_version_id,
                        last_seen_document_version_id=document_version_id,
                        metadata_json=None,
                    )
                    self.session.add(row)
                    existing_by_key[(node_row.id, alias_hash)] = row

                row.alias_label_he = alias_label_he
                row.alias_label_norm = alias_norm
                row.alias_kind = alias_kind
                row.confidence = max(row.confidence, _clamp_score(alias_confidence))
                if row.first_seen_document_version_id is None:
                    row.first_seen_document_version_id = document_version_id
                row.last_seen_document_version_id = document_version_id
                row.metadata_json = json.dumps(
                    {
                        "candidate_id": candidate_id,
                        "run_id": run_id,
                    },
                    ensure_ascii=False,
                )
                touched.add((node_row.id, alias_hash))

        return len(touched)

    def _upsert_mentions(
        self,
        *,
        run_id: int,
        document_id: int,
        document_version_id: int,
        source_kind: str,
        extracted_text: str,
        citation_map: list[dict[str, int]],
        source_nodes_by_id: dict[str, Any],
        candidate_to_node: dict[str, SemanticNode],
    ) -> tuple[dict[tuple[str, int], int], dict[str, list[SemanticMention]]]:
        mention_index_map: dict[tuple[str, int], int] = {}
        mentions_by_candidate: dict[str, list[SemanticMention]] = {candidate_id: [] for candidate_id in candidate_to_node}
        if not candidate_to_node:
            return mention_index_map, mentions_by_candidate

        node_ids = [row.id for row in candidate_to_node.values()]
        existing = self.session.execute(
            select(SemanticMention).where(
                SemanticMention.document_version_id == document_version_id,
                SemanticMention.semantic_node_id.in_(node_ids),
            )
        ).scalars().all()
        existing_by_key = {(row.semantic_node_id, row.start_offset, row.end_offset): row for row in existing}

        for candidate_id, node_row in candidate_to_node.items():
            source_node = source_nodes_by_id.get(candidate_id)
            if source_node is None:
                continue

            for idx, mention in enumerate(source_node.mentions):
                validation = self.canonicalizer.validate_mention_span(
                    extracted_text=extracted_text,
                    citation_map=citation_map,
                    mention_text=mention.mention_text,
                    start_offset=mention.start_offset,
                    end_offset=mention.end_offset,
                )
                if not validation.is_valid:
                    continue

                mention_text_norm = self.canonicalizer.normalize_text(mention.mention_text)
                if not mention_text_norm:
                    continue

                mention_confidence, mention_confidence_source = _resolve_mention_confidence(
                    mention_confidence=mention.confidence,
                    node_confidence=node_row.confidence,
                    normalized_match=validation.normalized_match,
                    page_resolved=validation.page_resolved,
                )

                key = (node_row.id, mention.start_offset, mention.end_offset)
                row = existing_by_key.get(key)
                if row is None:
                    row = SemanticMention(
                        semantic_node_id=node_row.id,
                        document_id=document_id,
                        document_version_id=document_version_id,
                        source_kind=source_kind,
                        start_offset=mention.start_offset,
                        end_offset=mention.end_offset,
                        start_page=validation.start_page,
                        end_page=validation.end_page,
                        mention_text=mention.mention_text,
                        mention_text_norm=mention_text_norm,
                        mention_confidence=mention_confidence,
                        evidence_hash=self.canonicalizer.mention_evidence_hash(
                            document_version_id=document_version_id,
                            semantic_node_id=node_row.id,
                            start_offset=mention.start_offset,
                            end_offset=mention.end_offset,
                            mention_text_norm=mention_text_norm,
                        ),
                        metadata_json=None,
                    )
                    self.session.add(row)
                    existing_by_key[key] = row

                row.document_id = document_id
                row.document_version_id = document_version_id
                row.source_kind = source_kind
                row.start_offset = mention.start_offset
                row.end_offset = mention.end_offset
                row.start_page = mention.start_page if mention.start_page is not None else validation.start_page
                row.end_page = mention.end_page if mention.end_page is not None else validation.end_page
                row.mention_text = mention.mention_text
                row.mention_text_norm = mention_text_norm
                row.mention_confidence = mention_confidence
                row.evidence_hash = self.canonicalizer.mention_evidence_hash(
                    document_version_id=document_version_id,
                    semantic_node_id=node_row.id,
                    start_offset=mention.start_offset,
                    end_offset=mention.end_offset,
                    mention_text_norm=mention_text_norm,
                )
                row.metadata_json = json.dumps(
                    {
                        "candidate_id": candidate_id,
                        "run_id": run_id,
                        "normalized_match": validation.normalized_match,
                        "page_resolved": validation.page_resolved,
                        "confidence_source": mention_confidence_source,
                        "evidence_span_ids": list(source_node.evidence_span_ids),
                    },
                    ensure_ascii=False,
                )
                self.session.flush()

                mention_index_map[(candidate_id, idx)] = row.id
                mentions_by_candidate.setdefault(candidate_id, []).append(row)

        return mention_index_map, mentions_by_candidate

    def _upsert_edges(
        self,
        *,
        run_id: int,
        output: SemanticExtractionOutput,
        candidate_to_node: dict[str, SemanticNode],
    ) -> int:
        touched: set[tuple[int, int, str]] = set()
        for edge in output.edges:
            source = candidate_to_node.get(edge.source_candidate_id)
            target = candidate_to_node.get(edge.target_candidate_id)
            if source is None or target is None or source.id == target.id:
                continue

            row = self.session.execute(
                select(SemanticEdge).where(
                    SemanticEdge.source_node_id == source.id,
                    SemanticEdge.target_node_id == target.id,
                    SemanticEdge.relation_type == edge.relation_type.value,
                )
            ).scalar_one_or_none()
            if row is None:
                row = SemanticEdge(
                    source_node_id=source.id,
                    target_node_id=target.id,
                    relation_type=edge.relation_type.value,
                    confidence=_clamp_score(edge.confidence),
                    provenance=edge.provenance,
                    metadata_json=None,
                )
                self.session.add(row)

            row.confidence = max(row.confidence, _clamp_score(edge.confidence))
            row.provenance = edge.provenance
            row.metadata_json = json.dumps(
                {
                    "run_id": run_id,
                },
                ensure_ascii=False,
            )
            touched.add((source.id, target.id, edge.relation_type.value))
        return len(touched)

    def _upsert_decision_links(
        self,
        *,
        run_id: int,
        output: SemanticExtractionOutput,
        candidate_to_node: dict[str, SemanticNode],
        mention_index_map: dict[tuple[str, int], int],
    ) -> int:
        if not output.decision_links:
            return 0

        candidate_decision_ids = sorted({item.decision_id for item in output.decision_links})
        valid_decision_ids = set(
            self.session.execute(
                select(Decision.id).where(Decision.id.in_(candidate_decision_ids))
            ).scalars().all()
        )

        touched: set[tuple[int, int, str]] = set()
        for link in output.decision_links:
            node = candidate_to_node.get(link.node_candidate_id)
            if node is None or link.decision_id not in valid_decision_ids:
                continue

            mention_id = None
            if link.mention_index is not None:
                mention_index = int(link.mention_index)
                mention_id = mention_index_map.get((link.node_candidate_id, mention_index))

            row = self.session.execute(
                select(DecisionSemanticLink).where(
                    DecisionSemanticLink.decision_id == link.decision_id,
                    DecisionSemanticLink.semantic_node_id == node.id,
                    DecisionSemanticLink.relation_role == link.relation_role.value,
                )
            ).scalar_one_or_none()
            if row is None:
                row = DecisionSemanticLink(
                    decision_id=link.decision_id,
                    semantic_node_id=node.id,
                    relation_role=link.relation_role.value,
                    confidence=_clamp_score(link.confidence),
                    source_mention_id=mention_id,
                    metadata_json=None,
                )
                self.session.add(row)

            row.confidence = max(row.confidence, _clamp_score(link.confidence))
            if mention_id is not None:
                row.source_mention_id = mention_id
            row.metadata_json = json.dumps(
                {
                    "candidate_id": link.node_candidate_id,
                    "run_id": run_id,
                },
                ensure_ascii=False,
            )
            touched.add((link.decision_id, node.id, link.relation_role.value))

        return len(touched)

    def _upsert_artifact_links(
        self,
        *,
        run_id: int,
        document_version_id: int,
        candidate_to_node: dict[str, SemanticNode],
        mention_rows_by_candidate: dict[str, list[SemanticMention]],
    ) -> int:
        artifact_rows = self.session.execute(
            select(
                RetrievalArtifact.artifact_id,
                RetrievalArtifact.start_offset,
                RetrievalArtifact.end_offset,
                RetrievalArtifact.artifact_kind,
            ).where(
                RetrievalArtifact.document_version_id == document_version_id,
                RetrievalArtifact.artifact_kind.in_(("header_anchor", "section_unit", "decision_unit")),
            )
        ).all()
        if not artifact_rows or not candidate_to_node:
            return 0

        artifacts = [
            {
                "artifact_id": artifact_id,
                "start_offset": start_offset,
                "end_offset": end_offset,
                "artifact_kind": artifact_kind,
            }
            for artifact_id, start_offset, end_offset, artifact_kind in artifact_rows
        ]
        artifact_ids = [item["artifact_id"] for item in artifacts]
        node_ids = [row.id for row in candidate_to_node.values()]

        existing = self.session.execute(
            select(ArtifactSemanticLink).where(
                ArtifactSemanticLink.artifact_id.in_(artifact_ids),
                ArtifactSemanticLink.semantic_node_id.in_(node_ids),
            )
        ).scalars().all()
        existing_by_key = {(row.artifact_id, row.semantic_node_id): row for row in existing}
        touched: set[tuple[str, int]] = set()

        for candidate_id, mention_rows in mention_rows_by_candidate.items():
            node = candidate_to_node.get(candidate_id)
            if node is None:
                continue
            for mention in mention_rows:
                for artifact in artifacts:
                    if not _spans_overlap(
                        mention.start_offset,
                        mention.end_offset,
                        artifact["start_offset"],
                        artifact["end_offset"],
                    ):
                        continue
                    key = (artifact["artifact_id"], node.id)
                    row = existing_by_key.get(key)
                    mention_link_confidence = _clamp_score(mention.mention_confidence)
                    if row is None:
                        row = ArtifactSemanticLink(
                            artifact_id=artifact["artifact_id"],
                            semantic_node_id=node.id,
                            confidence=mention_link_confidence,
                            source_mention_id=mention.id,
                            metadata_json=None,
                        )
                        self.session.add(row)
                        existing_by_key[key] = row

                    row.confidence = max(row.confidence, mention_link_confidence)
                    if mention.id is not None:
                        row.source_mention_id = mention.id
                    row.metadata_json = json.dumps(
                        {
                            "candidate_id": candidate_id,
                            "provenance": "mention_overlap",
                            "run_id": run_id,
                            "confidence_source": "mention_overlap",
                            "artifact_kind": artifact["artifact_kind"],
                        },
                        ensure_ascii=False,
                    )
                    touched.add(key)

        return len(touched)

    def _replace_reject_rows(
        self,
        *,
        run_id: int,
        canonical_report: CanonicalizationReport,
        output: SemanticExtractionOutput | None,
        validation_report: SemanticValidationReport | None,
    ) -> int:
        self.session.execute(
            delete(SemanticCandidateReject).where(
                SemanticCandidateReject.semantic_document_run_id == run_id
            )
        )

        records: list[dict[str, Any]] = []
        for row in canonical_report.rejected_nodes:
            records.append(
                {
                    "candidate_label_he": row.label_he,
                    "candidate_label_norm": row.label_norm,
                    "node_kind": row.node_kind,
                    "semantic_type": row.semantic_type,
                    "reason_code": (row.reject_reason.value if row.reject_reason else SemanticRejectReason.UNSUPPORTED_SCHEMA.value),
                    "model_confidence": row.confidence,
                    "metadata": {
                        "candidate_id": row.candidate_id,
                        "node_key_hash": row.node_key_hash,
                        "status": row.status,
                    },
                }
            )

        if output is not None:
            for reject in output.rejects:
                records.append(
                    {
                        "candidate_label_he": reject.candidate_label_he,
                        "candidate_label_norm": reject.candidate_label_norm,
                        "node_kind": reject.node_kind,
                        "semantic_type": reject.semantic_type,
                        "reason_code": reject.reason_code.value,
                        "model_confidence": reject.model_confidence,
                        "metadata": reject.metadata,
                    }
                )

        if validation_report is not None:
            for issue in validation_report.issues:
                records.append(
                    {
                        "candidate_label_he": None,
                        "candidate_label_norm": None,
                        "node_kind": None,
                        "semantic_type": None,
                        "reason_code": SemanticRejectReason.UNSUPPORTED_SCHEMA.value,
                        "model_confidence": None,
                        "metadata": {
                            "issue_code": issue.code,
                            "path": issue.path,
                            "message": issue.message,
                        },
                    }
                )

        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for record in records:
            dedupe_key = json.dumps(
                {
                    "candidate_label_norm": record["candidate_label_norm"],
                    "node_kind": record["node_kind"],
                    "semantic_type": record["semantic_type"],
                    "reason_code": record["reason_code"],
                    "metadata": record["metadata"],
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduped.append(record)

        for record in deduped:
            self.session.add(
                SemanticCandidateReject(
                    semantic_document_run_id=run_id,
                    candidate_label_he=record["candidate_label_he"],
                    candidate_label_norm=record["candidate_label_norm"],
                    node_kind=record["node_kind"],
                    semantic_type=record["semantic_type"],
                    reason_code=record["reason_code"],
                    model_confidence=record["model_confidence"],
                    metadata_json=json.dumps(record["metadata"], ensure_ascii=False),
                )
            )
        return len(deduped)


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
                "confidence_source": row.confidence_source,
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
                "confidence_source": row.confidence_source,
                "support_count": row.support_count,
                "status": row.status,
                "node_key_hash": row.node_key_hash,
                "reject_reason": row.reject_reason.value if row.reject_reason else None,
            }
            for row in report.rejected_nodes
        ],
        "warnings": report.warnings,
    }


def _resolve_mention_confidence(
    *,
    mention_confidence: float | None,
    node_confidence: float,
    normalized_match: bool,
    page_resolved: bool,
) -> tuple[float, str]:
    explicit = _normalize_optional_confidence(mention_confidence)
    if explicit is not None:
        return explicit, "model_mention"

    base = _clamp_score(node_confidence * 0.85)
    if normalized_match:
        base = max(base, 0.45)
    if page_resolved:
        base = min(1.0, base + 0.05)
    return _clamp_score(base), "derived_from_node"
def _spans_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return max(start_a, start_b) < min(end_a, end_b)


def _clamp_score(value: float | int | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _normalize_optional_confidence(value: float | int | None) -> float | None:
    if value is None:
        return None
    return _clamp_score(value)
