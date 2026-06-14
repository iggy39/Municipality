from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from municipality.gis_evidence_links import (
    decision_gis_feature_links,
    extract_plan_numbers,
    link_decisions_to_gis_plans,
    plan_related_decisions,
)
from municipality.migrations import apply_all
from municipality.models import (
    Decision,
    DecisionRequestContext,
    Document,
    DocumentVersion,
    Meeting,
    RetrievalArtifact,
    SourceSite,
)


def test_extract_plan_numbers_dedupes_exact_plan_mentions() -> None:
    assert extract_plan_numbers("תכנית 507-0073395", "507-0073395 וגם 101-0057273") == ["507-0073395", "101-0057273"]


def test_link_decisions_to_gis_plans_links_decision_and_feature(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'gis-links.db'}", future=True)
    apply_all(engine, Path("migrations"))
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE plans (
                  id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  plan_number TEXT NOT NULL,
                  plan_name TEXT,
                  metadata TEXT,
                  fetched_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO plans VALUES
                ('plan-1', 'xplan_blue_lines', '507-0073395', 'מגדל אלדן', :metadata, '2026-06-13')
                """
            ),
            {"metadata": json.dumps({"plan": {"station_desc": "אישור"}}, ensure_ascii=False)},
        )

    with Session(engine) as session, session.begin():
        site = SourceSite(municipality_slug="tel_aviv", name="Tel Aviv", root_url="https://example.test")
        session.add(site)
        session.flush()
        document = Document(
            source_site_id=site.id,
            document_external_id="doc-1",
            canonical_url="https://example.test/protocol.pdf",
            title_he="פרוטוקול בדיקה",
            doc_kind="protocol_full",
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        session.add(document)
        session.flush()
        version = DocumentVersion(document_id=document.id, sha256="a" * 64, byte_size=10, storage_uri="raw/protocol.pdf")
        session.add(version)
        session.flush()
        meeting = Meeting(source_site_id=site.id, meeting_external_id="meeting-1", title_he="ישיבה", meeting_date="2026-06-13")
        session.add(meeting)
        session.flush()
        decision = Decision(
            meeting_id=meeting.id,
            source_document_id=document.id,
            decision_number="1",
            agenda_item="תכנית 507-0073395",
            decision_text="הוחלט לאשר את תכנית 507-0073395",
            decision_signature_norm="approve-plan-507-0073395",
            parser_confidence=0.95,
            is_public=True,
        )
        session.add(decision)
        session.flush()
        artifact = RetrievalArtifact(
            artifact_id="artifact-plan-1",
            document_id=document.id,
            document_version_id=version.id,
            extracted_document_id=1,
            section_id=None,
            source_kind="pdf_first_protocol",
            artifact_kind="decision_unit",
            ordinal=1,
            title_he="סעיף תכנית",
            committee_name=None,
            meeting_date="2026-06-13",
            header_path_json="[]",
            body_text="הוחלט לאשר את תכנית 507-0073395",
            retrieval_text="הוחלט לאשר את תכנית 507-0073395",
            retrieval_text_norm="הוחלט לאשר את תכנית 507-0073395",
            start_offset=0,
            end_offset=35,
            start_page=2,
            end_page=2,
            citation_label="עמוד 2",
            trigram_count=1,
        )
        session.add(artifact)
        session.add(
            DecisionRequestContext(
                decision_id=decision.id,
                source_document_id=document.id,
                request_subject_he="תכנית 507-0073395",
                source_artifact_ids_json=json.dumps([artifact.artifact_id]),
                confidence=0.9,
            )
        )
        session.flush()
        summary = link_decisions_to_gis_plans(session)

        assert summary.scanned_decisions == 1
        assert summary.matched_decisions == 1
        assert summary.inserted_or_updated == 1
        decision_links = decision_gis_feature_links(session, decision_id=decision.id)
        assert decision_links[0]["plan_number"] == "507-0073395"
        assert decision_links[0]["feature_label"] == "מגדל אלדן"
        related = plan_related_decisions(session, plan_number="507-0073395")
        assert related[0]["decision_id"] == decision.id
        assert related[0]["evidence_ref"].startswith("artifact_")
