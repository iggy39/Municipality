from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from municipality.api import app, get_db
from municipality.migrations import apply_all
from municipality.models import Document, DocumentVersion, ExtractedDocument, RetrievalArtifact, SourceSite


def test_admin_ingestion_coverage_json_and_html_show_all_statuses(tmp_path: Path) -> None:
    db_path = tmp_path / "admin_ingestion_coverage.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        _seed_available_protocol(session)
        _seed_explicit_missing_notice(session)
        session.commit()

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        json_response = client.get("/api/admin/ingestion-coverage")
        html_response = client.get("/admin/ingestion-coverage")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert json_response.status_code == 200
    payload = json_response.json()
    ashdod = _municipality(payload, "ashdod")
    tel_aviv = _municipality(payload, "tel_aviv")

    assert _source_row(ashdod, "protocol")["status"] == "available"
    assert _source_row(ashdod, "protocol")["document_count"] == 1
    assert _source_row(ashdod, "protocol")["artifact_count"] == 1
    assert _source_row(tel_aviv, "public_notice")["status"] == "missing"
    assert _source_row(ashdod, "tender")["status"] == "unknown"
    assert payload["summary"]["available"] >= 1
    assert payload["summary"]["missing"] >= 1
    assert payload["summary"]["unknown"] >= 1

    assert html_response.status_code == 200
    body = html_response.text
    assert "Source coverage checklist" in body
    assert "אשדוד" in body
    assert "תל אביב-יפו" in body
    assert "Available" in body
    assert "Missing" in body
    assert "Unknown" in body
    assert "/api/admin/ingestion-coverage" in body


def _seed_available_protocol(session: Session) -> None:
    site = SourceSite(municipality_slug="ashdod", name="ashdod", root_url="https://example.local/ashdod")
    session.add(site)
    session.flush()
    document = Document(
        source_site_id=site.id,
        document_external_id="doc:ashdod-protocol",
        canonical_url="https://example.local/ashdod/protocol.pdf",
        title_he="פרוטוקול בדיקה",
        doc_kind="protocol_full",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    session.add(document)
    session.flush()
    version = DocumentVersion(
        document_id=document.id,
        sha256="a" * 64,
        byte_size=10,
        storage_uri="tree/ashdod/protocol.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add(version)
    session.flush()
    extracted = ExtractedDocument(
        document_version_id=version.id,
        parser_name="test",
        parser_version="1",
        status="completed",
        extracted_text="הוחלט לאשר בדיקה",
        page_count=1,
        pages_json=json.dumps([]),
        citation_map_json=json.dumps({}),
        quality_score=1.0,
        quality_flags_json=json.dumps([]),
        quality_summary_json=json.dumps({}),
        updated_at=datetime.utcnow(),
    )
    session.add(extracted)
    session.flush()
    session.add(
        RetrievalArtifact(
            artifact_id="admin-coverage-artifact-1",
            document_id=document.id,
            document_version_id=version.id,
            extracted_document_id=extracted.id,
            section_id=None,
            source_kind="pdf_first_v4_protocol",
            artifact_kind="pdf_first_v4_retrieval_chunk",
            ordinal=1,
            title_he="סעיף בדיקה",
            committee_name=None,
            meeting_date=None,
            header_path_json=json.dumps(["סעיף בדיקה"], ensure_ascii=False),
            body_text="הוחלט לאשר בדיקה",
            retrieval_text="הוחלט לאשר בדיקה",
            retrieval_text_norm="הוחלט לאשר בדיקה",
            start_offset=0,
            end_offset=18,
            start_page=1,
            end_page=1,
            citation_label="p.1",
            trigram_count=1,
            metadata_json=None,
        )
    )


def _seed_explicit_missing_notice(session: Session) -> None:
    session.add(SourceSite(municipality_slug="tel_aviv", name="tel_aviv", root_url="https://example.local/tel-aviv"))
    session.flush()
    session.execute(
        text(
            """
            INSERT INTO ingestion_source_coverage (
              municipality_slug, municipality_name_he, source_type, status, notes, checked_at
            ) VALUES (
              'tel_aviv', 'תל אביב-יפו', 'public_notice', 'missing', 'Checked public source list; no import target yet.', '2026-06-28'
            )
            """
        )
    )


def _municipality(payload: dict, slug: str) -> dict:
    return next(row for row in payload["municipalities"] if row["municipality_slug"] == slug)


def _source_row(municipality: dict, source_type: str) -> dict:
    return next(row for row in municipality["source_types"] if row["source_type"] == source_type)
