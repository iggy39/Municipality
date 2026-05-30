from __future__ import annotations

import argparse
import hashlib
import mimetypes
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

try:
    from .common import PROJECT_ROOT
except ImportError:  # pragma: no cover - direct script execution
    from common import PROJECT_ROOT

from municipality.db import build_engine
from municipality.migrations import apply_all
from municipality.models import AssetManifest, Document, DocumentVersion, PipelineRun, SourceSite
from municipality.processing import ProcessingService, SemanticEnrichmentPolicy
from municipality.storage import RawStorage


def main() -> int:
    parser = argparse.ArgumentParser(description="Import local municipal protocol PDFs for RAG eval indexing")
    parser.add_argument("--city", required=True, help="Municipality/city slug, for example ashdod")
    parser.add_argument("--input", type=Path, default=PROJECT_ROOT / "rag_eval" / "data" / "raw_docs")
    parser.add_argument("--limit", type=int, default=None, help="Maximum PDF files to import")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--storage-root", type=Path, default=PROJECT_ROOT / "storage" / "raw")
    parser.add_argument("--skip-process", action="store_true", help="Only register files; do not run extraction/indexing")
    args = parser.parse_args()

    input_dir = args.input
    if not input_dir.exists():
        raise SystemExit(f"input directory does not exist: {input_dir}")

    files = sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.casefold() == ".pdf")
    if args.limit is not None:
        files = files[: max(0, args.limit)]
    if not files:
        raise SystemExit(f"no PDF files found in {input_dir}")

    engine = build_engine(args.database_url)
    apply_all(engine, PROJECT_ROOT / "migrations")

    imported_doc_ids: list[int] = []
    with Session(engine) as session:
        site = _ensure_source_site(session=session, city=args.city)
        run = PipelineRun(run_type="rag_eval_import", municipality_slug=args.city, status="completed", finished_at=datetime.utcnow())
        session.add(run)
        session.flush()
        storage = RawStorage(args.storage_root)
        for file_path in files:
            doc_id = _import_pdf(session=session, storage=storage, site=site, city=args.city, file_path=file_path, run_id=run.id)
            imported_doc_ids.append(doc_id)
        session.commit()

        if not args.skip_process:
            processor = ProcessingService(
                session=session,
                storage_root=args.storage_root,
                semantic_policy=SemanticEnrichmentPolicy(enabled=False),
            )
            for doc_id in imported_doc_ids:
                processor.run(doc_id=doc_id, municipality_slug=args.city)

    engine.dispose()
    print(f"imported={len(imported_doc_ids)} processed={0 if args.skip_process else len(imported_doc_ids)}")
    return 0


def _ensure_source_site(*, session: Session, city: str) -> SourceSite:
    root_url = f"local://rag_eval/{city}"
    existing = session.execute(
        select(SourceSite).where(SourceSite.municipality_slug == city, SourceSite.root_url == root_url)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = SourceSite(municipality_slug=city, name=city, root_url=root_url)
    session.add(row)
    session.flush()
    return row


def _import_pdf(*, session: Session, storage: RawStorage, site: SourceSite, city: str, file_path: Path, run_id: int) -> int:
    payload = file_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    canonical_url = f"local://rag_eval/{city}/{file_path.name}"
    external_id = f"rag_eval:{digest[:24]}"
    mime = mimetypes.guess_type(file_path.name)[0] or "application/pdf"

    document = session.execute(select(Document).where(Document.canonical_url == canonical_url)).scalar_one_or_none()
    if document is None:
        document = Document(
            source_site_id=site.id,
            document_external_id=external_id,
            canonical_url=canonical_url,
            title_he=file_path.stem,
            doc_kind="protocol_full",
            mime_hint="application/pdf" if mime == "application/pdf" else mime,
            last_seen_at=datetime.utcnow(),
        )
        session.add(document)
        session.flush()
    else:
        document.last_seen_at = datetime.utcnow()

    manifest = session.execute(
        select(AssetManifest).where(
            AssetManifest.source_site_id == site.id,
            AssetManifest.asset_external_id == external_id,
        )
    ).scalar_one_or_none()
    if manifest is None:
        session.add(
            AssetManifest(
                source_site_id=site.id,
                source_node_external_id="rag_eval_protocols",
                asset_external_id=external_id,
                asset_url=canonical_url,
                asset_kind="protocol_full",
                title_he=file_path.stem,
                mime_hint="application/pdf",
                crawl_run_id=run_id,
                discovered_at=datetime.utcnow(),
            )
        )

    existing_version = session.execute(
        select(DocumentVersion).where(DocumentVersion.document_id == document.id, DocumentVersion.sha256 == digest)
    ).scalar_one_or_none()
    if existing_version is None:
        storage_uri = storage.write(city, "protocol_full", digest, payload)
        session.add(
            DocumentVersion(
                document_id=document.id,
                sha256=digest,
                byte_size=len(payload),
                storage_uri=storage_uri,
                fetched_http_status=200,
                fetched_mime="application/pdf",
            )
        )

    return int(document.id)


if __name__ == "__main__":
    raise SystemExit(main())
