from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session

from municipality.adapters import AshdodDiscoveryAdapter
from municipality.discovery import DiscoveryEngine, classify_asset_kind, normalize_url, resolve_external_id
from municipality.fetcher import AssetFetcher
from municipality.models import AssetManifest, Document, DocumentVersion, PipelineRun, PipelineRunStep, SourceSite, TaxonomyNode
from municipality.storage import RawStorage


class PipelineService:
    def __init__(
        self,
        session: Session,
        html_fetcher,
        fetcher: AssetFetcher,
        storage_root: Path,
        adapter=None,
    ):
        self.session = session
        self.discovery = DiscoveryEngine(html_fetcher=html_fetcher, adapter=adapter)
        self.fetcher = fetcher
        self.storage = RawStorage(storage_root)

    def ensure_source_site(self, municipality_slug: str, root_url: str) -> SourceSite:
        existing = self.session.execute(
            select(SourceSite).where(SourceSite.municipality_slug == municipality_slug, SourceSite.root_url == root_url)
        ).scalar_one_or_none()
        if existing:
            return existing
        row = SourceSite(municipality_slug=municipality_slug, name=municipality_slug, root_url=root_url)
        self.session.add(row)
        self.session.flush()
        return row

    def run_crawl(self, municipality_slug: str, root_url: str) -> int:
        if municipality_slug.lower() == "ashdod" and self.discovery.adapter is None:
            self.discovery.adapter = AshdodDiscoveryAdapter()

        run = PipelineRun(run_type="crawl", municipality_slug=municipality_slug, status="running")
        self.session.add(run)
        self.session.flush()

        site = self.ensure_source_site(municipality_slug, root_url)
        nodes, assets = self.discovery.crawl(root_url)

        for node in nodes:
            existing = self.session.execute(
                select(TaxonomyNode).where(
                    TaxonomyNode.source_site_id == site.id,
                    TaxonomyNode.node_external_id == node.external_id,
                )
            ).scalar_one_or_none()
            if not existing:
                self.session.add(
                    TaxonomyNode(
                        source_site_id=site.id,
                        node_external_id=node.external_id,
                        parent_external_id=node.parent_external_id,
                        title_he=node.title_he,
                        canonical_url=node.canonical_url,
                        node_type=node.node_type,
                        depth=node.depth,
                        count_hint=node.count_hint,
                        crawl_run_id=run.id,
                        discovered_at=node.discovered_at,
                    )
                )

        for asset in assets:
            step = PipelineRunStep(run_id=run.id, step_name="fetch", status="running", item_ref=asset.canonical_url)
            self.session.add(step)
            self.session.flush()

            kind = classify_asset_kind(asset.title_he, asset.mime_hint)
            canonical_url = normalize_url(asset.canonical_url)
            ext_id = resolve_external_id(canonical_url)

            manifest_row = self.session.execute(
                select(AssetManifest).where(
                    AssetManifest.source_site_id == site.id,
                    AssetManifest.asset_external_id == ext_id,
                )
            ).scalar_one_or_none()
            if not manifest_row:
                self.session.add(
                    AssetManifest(
                        source_site_id=site.id,
                        source_node_external_id=asset.source_node_external_id,
                        asset_external_id=ext_id,
                        asset_url=canonical_url,
                        asset_kind=kind,
                        title_he=asset.title_he,
                        mime_hint=asset.mime_hint,
                        crawl_run_id=run.id,
                        discovered_at=asset.discovered_at,
                    )
                )

            document = self.session.execute(select(Document).where(Document.canonical_url == canonical_url)).scalar_one_or_none()
            if not document:
                document = Document(
                    source_site_id=site.id,
                    document_external_id=ext_id,
                    canonical_url=canonical_url,
                    title_he=asset.title_he,
                    doc_kind=kind,
                    mime_hint=asset.mime_hint,
                    last_seen_at=datetime.utcnow(),
                )
                self.session.add(document)
                self.session.flush()
            else:
                document.last_seen_at = datetime.utcnow()

            if asset.mime_hint != "application/pdf":
                step.status = "skipped"
                step.detail = "UNSUPPORTED_MIME"
                continue

            result = self.fetcher.fetch(asset.canonical_url)
            if not result.ok:
                step.status = "failed"
                step.detail = result.reason
                continue

            digest = hashlib.sha256(result.body).hexdigest()
            existing_version = self.session.execute(
                select(DocumentVersion).where(DocumentVersion.document_id == document.id, DocumentVersion.sha256 == digest)
            ).scalar_one_or_none()

            if existing_version:
                step.status = "completed"
                step.detail = "ALREADY_EXISTS"
            else:
                storage_uri = self.storage.write(municipality_slug, kind, digest, result.body)
                self.session.add(
                    DocumentVersion(
                        document_id=document.id,
                        sha256=digest,
                        byte_size=len(result.body),
                        storage_uri=storage_uri,
                        fetched_http_status=result.status_code,
                        fetched_mime=result.mime,
                    )
                )
                step.status = "completed"
                step.detail = "NEW_VERSION"

        run.status = "completed"
        run.finished_at = datetime.utcnow()
        self.session.commit()
        return run.id
