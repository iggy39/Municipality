from __future__ import annotations

from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from municipality.db import Base


class SourceSite(Base):
    __tablename__ = "source_site"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    municipality_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    root_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class PipelineRun(Base):
    __tablename__ = "pipeline_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_type: Mapped[str] = mapped_column(String(32), nullable=False)
    municipality_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class PipelineRunStep(Base):
    __tablename__ = "pipeline_run_step"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_run.id"), nullable=False, index=True)
    step_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    item_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class TaxonomyNode(Base):
    __tablename__ = "taxonomy_node"
    __table_args__ = (
        UniqueConstraint("source_site_id", "node_external_id", name="uq_taxonomy_site_external"),
        Index("ix_taxonomy_node_external_id", "node_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_site_id: Mapped[int] = mapped_column(ForeignKey("source_site.id"), nullable=False)
    node_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title_he: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    count_hint: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crawl_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_run.id"), nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("canonical_url", name="uq_document_canonical_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_site_id: Mapped[int] = mapped_column(ForeignKey("source_site.id"), nullable=False)
    document_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    title_he: Mapped[str] = mapped_column(Text, nullable=False)
    doc_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    mime_hint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class AssetManifest(Base):
    __tablename__ = "asset_manifest"
    __table_args__ = (
        UniqueConstraint("source_site_id", "asset_external_id", name="uq_asset_manifest_site_external"),
        Index("ix_asset_manifest_crawl_run_id", "crawl_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_site_id: Mapped[int] = mapped_column(ForeignKey("source_site.id"), nullable=False)
    source_node_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_url: Mapped[str] = mapped_column(Text, nullable=False)
    asset_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title_he: Mapped[str] = mapped_column(Text, nullable=False)
    mime_hint: Mapped[str] = mapped_column(String(128), nullable=False)
    crawl_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_run.id"), nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class DocumentVersion(Base):
    __tablename__ = "document_version"
    __table_args__ = (
        UniqueConstraint("document_id", "sha256", name="uq_document_version_hash"),
        Index("ix_document_version_sha256", "sha256"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id"), nullable=False, index=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_mime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ExtractedDocument(Base):
    __tablename__ = "extracted_document"
    __table_args__ = (
        UniqueConstraint("document_version_id", name="uq_extracted_document_docver"),
        Index("ix_extracted_document_docver_id", "document_version_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_version_id: Mapped[int] = mapped_column(ForeignKey("document_version.id"), nullable=False)
    parser_name: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    citation_map_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_flags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    warning_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
class DocumentSection(Base):
    __tablename__ = "document_section"
    __table_args__ = (
        UniqueConstraint("section_id", name="uq_document_section_section_id"),
        Index("ix_document_section_docver_id", "document_version_id"),
        Index("ix_document_section_document_id", "document_id"),
        Index("ix_document_section_parent_id", "parent_section_id"),
        Index("ix_document_section_node_type", "node_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    section_id: Mapped[str] = mapped_column(String(64), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id"), nullable=False)
    document_version_id: Mapped[int] = mapped_column(ForeignKey("document_version.id"), nullable=False)
    extracted_document_id: Mapped[int] = mapped_column(ForeignKey("extracted_document.id"), nullable=False)
    parent_section_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    header_text: Mapped[str] = mapped_column(Text, nullable=False)
    header_text_norm: Mapped[str] = mapped_column(Text, nullable=False)
    header_level: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path_json: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    start_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class RetrievalArtifact(Base):
    __tablename__ = "retrieval_artifact"
    __table_args__ = (
        UniqueConstraint("artifact_id", name="uq_retrieval_artifact_artifact_id"),
        Index("ix_retrieval_artifact_docver_id", "document_version_id"),
        Index("ix_retrieval_artifact_document_id", "document_id"),
        Index("ix_retrieval_artifact_section_id", "section_id"),
        Index("ix_retrieval_artifact_source_kind", "source_kind"),
        Index("ix_retrieval_artifact_kind", "artifact_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id"), nullable=False)
    document_version_id: Mapped[int] = mapped_column(ForeignKey("document_version.id"), nullable=False)
    extracted_document_id: Mapped[int] = mapped_column(ForeignKey("extracted_document.id"), nullable=False)
    section_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    title_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    committee_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    meeting_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    header_path_json: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_text: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_text_norm: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    start_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    citation_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigram_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ArtifactTopicAnnotation(Base):
    __tablename__ = "artifact_topic_annotation"
    __table_args__ = (
        UniqueConstraint("artifact_id", name="uq_artifact_topic_annotation_artifact_id"),
        Index("ix_artifact_topic_annotation_primary_norm", "primary_topic_norm"),
        Index("ix_artifact_topic_annotation_route", "classifier_route"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[str] = mapped_column(String(64), ForeignKey("retrieval_artifact.artifact_id"), nullable=False)
    structural_topic_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    structural_topic_norm: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_topic_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_topic_norm: Mapped[str | None] = mapped_column(Text, nullable=True)
    secondary_topics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    section_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    classifier_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    classifier_route: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

class RetrievalArtifactEmbedding(Base):
    __tablename__ = "retrieval_artifact_embedding"
    __table_args__ = (
        UniqueConstraint(
            "artifact_id",
            "model_provider",
            "model_name",
            "dimensions",
            name="uq_retrieval_artifact_embedding_model",
        ),
        Index(
            "ix_retrieval_artifact_embedding_artifact_id",
            "artifact_id",
            "model_provider",
            "model_name",
            "dimensions",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Meeting(Base):
    __tablename__ = "meeting"
    __table_args__ = (
        UniqueConstraint("source_site_id", "meeting_external_id", name="uq_meeting_site_external"),
        Index("ix_meeting_source_site_id", "source_site_id"),
        Index("ix_meeting_external_id", "meeting_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_site_id: Mapped[int] = mapped_column(ForeignKey("source_site.id"), nullable=False)
    meeting_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title_he: Mapped[str] = mapped_column(Text, nullable=False)
    committee_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    meeting_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    meeting_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meeting_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parse_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Decision(Base):
    __tablename__ = "decision"
    __table_args__ = (
        Index("ix_decision_meeting_id", "meeting_id"),
        Index("ix_decision_source_document_id", "source_document_id"),
        Index("ix_decision_signature_norm", "decision_signature_norm"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meeting.id"), nullable=False)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("document.id"), nullable=False)
    decision_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agenda_item: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_text: Mapped[str] = mapped_column(Text, nullable=False)
    decision_signature_norm: Mapped[str] = mapped_column(String(255), nullable=False)
    parser_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Vote(Base):
    __tablename__ = "vote"
    __table_args__ = (
        UniqueConstraint("decision_id", name="uq_vote_decision"),
        Index("ix_vote_decision_id", "decision_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("decision.id"), nullable=False)
    for_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    against_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    abstain_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unanimous: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_uncertain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class DecisionCitation(Base):
    __tablename__ = "decision_citation"
    __table_args__ = (
        Index("ix_decision_citation_decision_id", "decision_id"),
        Index("ix_decision_citation_document_id", "document_id"),
        Index("ix_decision_citation_page_number", "page_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("decision.id"), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id"), nullable=False)
    document_version_id: Mapped[int | None] = mapped_column(ForeignKey("document_version.id"), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    anchor_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    anchor_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class MeetingDocumentLink(Base):
    __tablename__ = "meeting_document_link"
    __table_args__ = (
        UniqueConstraint("meeting_id", "document_id", name="uq_meeting_document_link"),
        Index("ix_meeting_document_link_meeting_id", "meeting_id"),
        Index("ix_meeting_document_link_document_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meeting.id"), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class DecisionDocumentLink(Base):
    __tablename__ = "decision_document_link"
    __table_args__ = (
        UniqueConstraint("decision_id", "document_id", name="uq_decision_document_link"),
        Index("ix_decision_document_link_decision_id", "decision_id"),
        Index("ix_decision_document_link_document_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("decision.id"), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class DecisionRequestContext(Base):
    __tablename__ = "decision_request_context"
    __table_args__ = (
        UniqueConstraint("decision_id", name="uq_decision_request_context_decision"),
        Index("ix_decision_request_context_source_document_id", "source_document_id"),
        Index("ix_decision_request_context_subject_topic_he", "subject_topic_he"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("decision.id"), nullable=False)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("document.id"), nullable=False)
    request_subject_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_topic_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    gush: Mapped[str | None] = mapped_column(String(64), nullable=True)
    helka: Mapped[str | None] = mapped_column(String(64), nullable=True)
    migrash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_artifact_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class DecisionExtractionCache(Base):
    __tablename__ = "decision_extraction_cache"
    __table_args__ = (
        UniqueConstraint("document_version_id", "cache_key", name="uq_decision_extraction_cache_docver_key"),
        Index("ix_decision_extraction_cache_docver", "document_version_id"),
        Index("ix_decision_extraction_cache_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_version_id: Mapped[int] = mapped_column(ForeignKey("document_version.id"), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    selected_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_reasons_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidates_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class QueryEmbeddingCache(Base):
    __tablename__ = "query_embedding_cache"
    __table_args__ = (
        UniqueConstraint(
            "text_hash",
            "query_kind",
            "model_provider",
            "model_name",
            "dimensions",
            name="uq_query_embedding_cache_key",
        ),
        Index(
            "ix_query_embedding_cache_lookup",
            "text_hash",
            "query_kind",
            "model_provider",
            "model_name",
            "dimensions",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
class RagAnswerCache(Base):
    __tablename__ = "rag_answer_cache"
    __table_args__ = (
        Index(
            "ix_rag_answer_cache_retrieval",
            "retrieval_set_id",
            "embedding_provider",
            "embedding_model",
            "embedding_dimensions",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    retrieval_set_id: Mapped[str] = mapped_column(String(96), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_query: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    query_embedding_json: Mapped[str] = mapped_column(Text, nullable=False)
    answer_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    answer_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    answer_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SemanticDocumentRun(Base):
    __tablename__ = "semantic_document_run"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "prompt_hash",
            "model_provider",
            "model_name",
            name="uq_semantic_document_run_call_key",
        ),
        Index("ix_semantic_document_run_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_version_id: Mapped[int] = mapped_column(ForeignKey("document_version.id"), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    api_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonicalization_report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SemanticNode(Base):
    __tablename__ = "semantic_node"
    __table_args__ = (
        UniqueConstraint("source_site_id", "node_key_hash", name="uq_semantic_node_site_hash"),
        UniqueConstraint(
            "source_site_id",
            "parent_node_id",
            "node_kind",
            "semantic_type",
            "pref_label_norm",
            name="uq_semantic_node_hierarchy_label",
        ),
        Index("ix_semantic_node_parent_node_id", "parent_node_id"),
        Index("ix_semantic_node_node_kind", "node_kind"),
        Index("ix_semantic_node_semantic_type", "semantic_type"),
        Index("ix_semantic_node_depth", "depth"),
        Index("ix_semantic_node_status", "status"),
        Index("ix_semantic_node_pref_label_norm", "pref_label_norm"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_site_id: Mapped[int] = mapped_column(ForeignKey("source_site.id"), nullable=False)
    node_key_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    node_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    semantic_type: Mapped[str] = mapped_column(String(64), nullable=False)
    pref_label_he: Mapped[str] = mapped_column(Text, nullable=False)
    pref_label_norm: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_node_id: Mapped[int | None] = mapped_column(ForeignKey("semantic_node.id"), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    specificity_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    support_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    first_seen_document_version_id: Mapped[int | None] = mapped_column(ForeignKey("document_version.id"), nullable=True)
    last_seen_document_version_id: Mapped[int | None] = mapped_column(ForeignKey("document_version.id"), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SemanticAlias(Base):
    __tablename__ = "semantic_alias"
    __table_args__ = (
        UniqueConstraint("semantic_node_id", "alias_hash", name="uq_semantic_alias_node_hash"),
        Index("ix_semantic_alias_label_norm", "alias_label_norm"),
        Index("ix_semantic_alias_kind", "alias_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    semantic_node_id: Mapped[int] = mapped_column(ForeignKey("semantic_node.id"), nullable=False)
    alias_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    alias_label_he: Mapped[str] = mapped_column(Text, nullable=False)
    alias_label_norm: Mapped[str] = mapped_column(String(255), nullable=False)
    alias_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    first_seen_document_version_id: Mapped[int | None] = mapped_column(ForeignKey("document_version.id"), nullable=True)
    last_seen_document_version_id: Mapped[int | None] = mapped_column(ForeignKey("document_version.id"), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SemanticEdge(Base):
    __tablename__ = "semantic_edge"
    __table_args__ = (
        UniqueConstraint("source_node_id", "target_node_id", "relation_type", name="uq_semantic_edge_triplet"),
        Index("ix_semantic_edge_source_node_id", "source_node_id"),
        Index("ix_semantic_edge_target_node_id", "target_node_id"),
        Index("ix_semantic_edge_relation_type", "relation_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_node_id: Mapped[int] = mapped_column(ForeignKey("semantic_node.id"), nullable=False)
    target_node_id: Mapped[int] = mapped_column(ForeignKey("semantic_node.id"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    provenance: Mapped[str] = mapped_column(String(24), nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SemanticMention(Base):
    __tablename__ = "semantic_mention"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "semantic_node_id",
            "start_offset",
            "end_offset",
            name="uq_semantic_mention_doc_node_span",
        ),
        Index("ix_semantic_mention_document_id", "document_id"),
        Index("ix_semantic_mention_semantic_node_id", "semantic_node_id"),
        Index("ix_semantic_mention_evidence_hash", "evidence_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    semantic_node_id: Mapped[int] = mapped_column(ForeignKey("semantic_node.id"), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id"), nullable=False)
    document_version_id: Mapped[int] = mapped_column(ForeignKey("document_version.id"), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    start_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mention_text: Mapped[str] = mapped_column(Text, nullable=False)
    mention_text_norm: Mapped[str] = mapped_column(Text, nullable=False)
    mention_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class DecisionSemanticLink(Base):
    __tablename__ = "decision_semantic_link"
    __table_args__ = (
        UniqueConstraint("decision_id", "semantic_node_id", "relation_role", name="uq_decision_semantic_link_triplet"),
        Index("ix_decision_semantic_link_decision_id", "decision_id"),
        Index("ix_decision_semantic_link_semantic_node_id", "semantic_node_id"),
        Index("ix_decision_semantic_link_relation_role", "relation_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("decision.id"), nullable=False)
    semantic_node_id: Mapped[int] = mapped_column(ForeignKey("semantic_node.id"), nullable=False)
    relation_role: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_mention_id: Mapped[int | None] = mapped_column(ForeignKey("semantic_mention.id"), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
class ArtifactSemanticLink(Base):
    __tablename__ = "artifact_semantic_link"
    __table_args__ = (
        UniqueConstraint("artifact_id", "semantic_node_id", name="uq_artifact_semantic_link_pair"),
        Index("ix_artifact_semantic_link_artifact_id", "artifact_id"),
        Index("ix_artifact_semantic_link_semantic_node_id", "semantic_node_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[str] = mapped_column(String(64), ForeignKey("retrieval_artifact.artifact_id"), nullable=False)
    semantic_node_id: Mapped[int] = mapped_column(ForeignKey("semantic_node.id"), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_mention_id: Mapped[int | None] = mapped_column(ForeignKey("semantic_mention.id"), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class TopicDecisionRun(Base):
    __tablename__ = "topic_decision_run"
    __table_args__ = (
        Index("ix_topic_decision_run_municipality", "municipality_slug", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    municipality_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    write_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_artifact_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extraction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_decision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class TopicDecision(Base):
    __tablename__ = "topic_decision"
    __table_args__ = (
        UniqueConstraint("run_id", "artifact_id", "semantic_node_id", "decision_index", name="uq_topic_decision_run_artifact_index"),
        Index("ix_topic_decision_artifact", "artifact_id"),
        Index("ix_topic_decision_semantic_node", "semantic_node_id"),
        Index("ix_topic_decision_codelists", "decision_kind_code", "outcome_status_code", "legal_effect_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("topic_decision_run.id"), nullable=False)
    municipality_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(64), ForeignKey("retrieval_artifact.artifact_id"), nullable=False)
    semantic_node_id: Mapped[int] = mapped_column(ForeignKey("semantic_node.id"), nullable=False)
    decision_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    root_topic_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    child_topic_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    topic_label_he: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("document.id"), nullable=False)
    source_document_version_id: Mapped[int] = mapped_column(ForeignKey("document_version.id"), nullable=False)
    source_page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_title_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_text_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_summary_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_kind_code: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_kind_label_he: Mapped[str] = mapped_column(Text, nullable=False)
    outcome_status_code: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome_status_label_he: Mapped[str] = mapped_column(Text, nullable=False)
    legal_effect_code: Mapped[str] = mapped_column(String(64), nullable=False)
    legal_effect_label_he: Mapped[str] = mapped_column(Text, nullable=False)
    primary_time_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    primary_time_start: Mapped[str | None] = mapped_column(String(32), nullable=True)
    primary_time_end: Mapped[str | None] = mapped_column(String(32), nullable=True)
    primary_time_precision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    primary_time_label_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_quote_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_anchors_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_refs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    resident_evidence_links_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    dicta_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class TopicDecisionQualityReport(Base):
    __tablename__ = "topic_decision_quality_report"
    __table_args__ = (
        Index("ix_topic_decision_quality_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("topic_decision_run.id"), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(64), ForeignKey("retrieval_artifact.artifact_id"), nullable=False)
    semantic_node_id: Mapped[int] = mapped_column(ForeignKey("semantic_node.id"), nullable=False)
    topic_label_he: Mapped[str] = mapped_column(Text, nullable=False)
    real_text: Mapped[str] = mapped_column(Text, nullable=False)
    decision_by_dicta: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_ground_truth: Mapped[str] = mapped_column(Text, nullable=False)
    reason_for_failure: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class TopicSubjectRun(Base):
    __tablename__ = "topic_subject_run"
    __table_args__ = (
        Index("ix_topic_subject_run_municipality", "municipality_slug", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    municipality_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    write_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_artifact_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extraction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_subject_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_decision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class TopicSubject(Base):
    __tablename__ = "topic_subject"
    __table_args__ = (
        UniqueConstraint("run_id", "artifact_id", "semantic_node_id", "subject_index", name="uq_topic_subject_run_artifact_index"),
        Index("ix_topic_subject_artifact", "artifact_id"),
        Index("ix_topic_subject_semantic_node", "semantic_node_id"),
        Index("ix_topic_subject_labels", "subject_root_label_norm", "subject_child_label_norm"),
        Index("ix_topic_subject_decision", "is_decision", "decision_label_norm"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("topic_subject_run.id"), nullable=False)
    municipality_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(64), ForeignKey("retrieval_artifact.artifact_id"), nullable=False)
    semantic_node_id: Mapped[int] = mapped_column(ForeignKey("semantic_node.id"), nullable=False)
    subject_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    root_topic_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    child_topic_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    topic_label_he: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("document.id"), nullable=False)
    source_document_version_id: Mapped[int] = mapped_column(ForeignKey("document_version.id"), nullable=False)
    source_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_root_label_he: Mapped[str] = mapped_column(Text, nullable=False)
    subject_root_label_norm: Mapped[str] = mapped_column(Text, nullable=False)
    subject_child_label_he: Mapped[str] = mapped_column(Text, nullable=False)
    subject_child_label_norm: Mapped[str] = mapped_column(Text, nullable=False)
    subject_object_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_details_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    topic_relevance: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    event_topic_label_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    anchor_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linked_event_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    link_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    link_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_summary_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    what_text_is_about_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_status: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate")
    is_decision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    decision_label_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_label_norm: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_summary_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_source_quote_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_refs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    resident_evidence_links_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    neighbor_contexts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    dicta_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class TopicSubjectQualityReport(Base):
    __tablename__ = "topic_subject_quality_report"
    __table_args__ = (
        Index("ix_topic_subject_quality_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("topic_subject_run.id"), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(64), ForeignKey("retrieval_artifact.artifact_id"), nullable=False)
    semantic_node_id: Mapped[int] = mapped_column(ForeignKey("semantic_node.id"), nullable=False)
    topic_label_he: Mapped[str] = mapped_column(Text, nullable=False)
    real_text: Mapped[str] = mapped_column(Text, nullable=False)
    what_text_is_about_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    topic_relevance: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    event_topic_label_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    anchor_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linked_event_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    link_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    link_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_root_by_dicta: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_child_by_dicta: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_object_by_dicta: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_details_by_dicta: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_by_dicta: Mapped[str | None] = mapped_column(Text, nullable=True)
    my_judgment: Mapped[str] = mapped_column(Text, nullable=False)
    ground_truth: Mapped[str] = mapped_column(Text, nullable=False)
    reason_for_failure: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SemanticCandidateReject(Base):
    __tablename__ = "semantic_candidate_reject"
    __table_args__ = (
        Index("ix_semantic_candidate_reject_run_id", "semantic_document_run_id"),
        Index("ix_semantic_candidate_reject_reason_code", "reason_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    semantic_document_run_id: Mapped[int] = mapped_column(ForeignKey("semantic_document_run.id"), nullable=False)
    candidate_label_he: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_label_norm: Mapped[str | None] = mapped_column(String(255), nullable=True)
    node_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    semantic_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    model_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
