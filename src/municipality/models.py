from __future__ import annotations

from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Index
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
