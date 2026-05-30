from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

try:
    from .common import RAG_EVAL_ROOT, write_jsonl
except ImportError:  # pragma: no cover - direct script execution
    from common import RAG_EVAL_ROOT, write_jsonl

from municipality.chunking import build_chunks
from municipality.db import build_engine
from municipality.extraction import PdfTextExtractor, parse_extracted_text
from municipality.models import Document, RetrievalArtifact, SourceSite


DATE_RE = re.compile(r"(20\d{2})[-_. /]?(0?[1-9]|1[0-2])[-_. /]?([0-2]?\d|3[01])")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract/export Hebrew municipal RAG chunks to JSONL")
    parser.add_argument("--source", choices=("db", "raw_docs"), default="db")
    parser.add_argument("--input", type=Path, default=RAG_EVAL_ROOT / "data" / "raw_docs")
    parser.add_argument("--output", type=Path, default=RAG_EVAL_ROOT / "data" / "chunks.jsonl")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--city", default=None)
    parser.add_argument("--source-kind", default="protocol")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-chars", type=int, default=180)
    args = parser.parse_args()

    if args.source == "db":
        rows = export_db_chunks(
            database_url=args.database_url,
            city=args.city,
            source_kind=args.source_kind,
            limit=args.limit,
            min_chars=args.min_chars,
        )
    else:
        rows = extract_raw_doc_chunks(
            input_dir=args.input,
            city=args.city or "local",
            limit=args.limit,
            min_chars=args.min_chars,
        )

    write_jsonl(args.output, rows)
    print(f"wrote {len(rows)} chunks to {args.output}")
    if args.source == "raw_docs":
        print("warning: raw_docs chunk IDs are not indexed in /ask unless these documents are also imported into the DB")
    return 0


def export_db_chunks(
    *,
    database_url: str | None,
    city: str | None,
    source_kind: str,
    limit: int | None,
    min_chars: int,
) -> list[dict[str, Any]]:
    engine = build_engine(database_url)
    rows: list[dict[str, Any]] = []
    with Session(engine) as session:
        stmt = (
            select(RetrievalArtifact, Document, SourceSite)
            .join(Document, Document.id == RetrievalArtifact.document_id)
            .join(SourceSite, SourceSite.id == Document.source_site_id)
            .where(RetrievalArtifact.source_kind == source_kind)
            .order_by(RetrievalArtifact.document_id.asc(), RetrievalArtifact.ordinal.asc())
        )
        if city:
            stmt = stmt.where(SourceSite.municipality_slug == city)
        if limit is not None:
            stmt = stmt.limit(max(0, limit))
        for artifact, document, site in session.execute(stmt).all():
            text = str(artifact.retrieval_text or artifact.body_text or "").strip()
            if len(text) < min_chars:
                continue
            rows.append(
                {
                    "doc_id": str(document.id),
                    "document_version_id": int(artifact.document_version_id),
                    "city": site.municipality_slug,
                    "date": artifact.meeting_date or _extract_date(document.title_he),
                    "chunk_id": str(artifact.artifact_id),
                    "text": text,
                    "page_number": artifact.start_page,
                    "end_page_number": artifact.end_page,
                    "source_kind": artifact.source_kind,
                    "artifact_kind": artifact.artifact_kind,
                    "document_title": document.title_he,
                    "document_url": document.canonical_url,
                    "header_path": _loads_json_list(artifact.header_path_json),
                }
            )
    engine.dispose()
    return rows


def extract_raw_doc_chunks(*, input_dir: Path, city: str, limit: int | None, min_chars: int) -> list[dict[str, Any]]:
    if not input_dir.exists():
        raise SystemExit(f"input directory does not exist: {input_dir}")
    files = sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.casefold() in {".pdf", ".txt"})
    if limit is not None:
        files = files[: max(0, limit)]
    extractor = PdfTextExtractor()
    rows: list[dict[str, Any]] = []
    for doc_index, file_path in enumerate(files, start=1):
        if file_path.suffix.casefold() == ".pdf":
            extraction = extractor.extract(file_path.read_bytes())
            full_text = extraction.full_text
            citation_map = extraction.citation_map
        else:
            full_text, _pages, citation_map = parse_extracted_text(file_path.read_text(encoding="utf-8"))
        chunks = build_chunks(
            document_version_id=doc_index,
            text=full_text,
            citation_map=citation_map,
            source_kind="protocol",
        )
        for chunk in chunks:
            text = str(chunk.get("chunk_text") or "").strip()
            if len(text) < min_chars:
                continue
            rows.append(
                {
                    "doc_id": file_path.stem,
                    "document_version_id": doc_index,
                    "city": city,
                    "date": _extract_date(file_path.stem),
                    "chunk_id": str(chunk["chunk_id"]),
                    "text": text,
                    "page_number": chunk.get("start_page"),
                    "end_page_number": chunk.get("end_page"),
                    "source_kind": "protocol",
                    "artifact_kind": "raw_chunk",
                    "document_title": file_path.stem,
                    "document_url": file_path.as_posix(),
                    "header_path": [],
                }
            )
    return rows


def _extract_date(value: str | None) -> str | None:
    match = DATE_RE.search(value or "")
    if not match:
        return None
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _loads_json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


if __name__ == "__main__":
    raise SystemExit(main())
