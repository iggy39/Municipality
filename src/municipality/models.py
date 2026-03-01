from __future__ import annotations

from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Index
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


class TextChunk(Base):
    __tablename__ = "text_chunk"
    __table_args__ = (
        UniqueConstraint("chunk_id", name="uq_text_chunk_chunk_id"),
        Index("ix_text_chunk_docver_id", "document_version_id"),
        Index("ix_text_chunk_document_id", "document_id"),
        Index("ix_text_chunk_source_kind", "source_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chunk_id: Mapped[str] = mapped_column(String(64), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id"), nullable=False)
    document_version_id: Mapped[int] = mapped_column(ForeignKey("document_version.id"), nullable=False)
    extracted_document_id: Mapped[int] = mapped_column(ForeignKey("extracted_document.id"), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_text_norm: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    start_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    citation_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigram_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


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
