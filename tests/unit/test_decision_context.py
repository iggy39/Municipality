from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.decision_context import DecisionContextService
from municipality.migrations import apply_all
from municipality.models import (
    Decision,
    DecisionCitation,
    DecisionRequestContext,
    Document,
    DocumentVersion,
    ExtractedDocument,
    Meeting,
    RetrievalArtifact,
    SourceSite,
)


def test_decision_context_service_links_request_subject_and_parcel(tmp_path: Path) -> None:
    db_path = tmp_path / "decision_context.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()

        document = Document(
            source_site_id=site.id,
            document_external_id="doc:alloc:1",
            canonical_url="https://example.local/alloc.pdf",
            title_he="פרוטוקול ועדת משנה להקצאות קרקע",
            doc_kind="protocol_full",
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        session.add(document)
        session.flush()

        version = DocumentVersion(
            document_id=document.id,
            sha256="a" * 64,
            byte_size=100,
            storage_uri="dummy.pdf",
            fetched_http_status=200,
            fetched_mime="application/pdf",
        )
        meeting = Meeting(
            source_site_id=site.id,
            meeting_external_id="meeting:1",
            title_he="ישיבת הקצאות",
            committee_name="ועדת הקצאות",
            meeting_kind="committee",
            meeting_code="1-25",
            meeting_date="2025-04-29",
            parse_confidence=0.9,
            metadata_json="{}",
            updated_at=datetime.utcnow(),
        )
        session.add_all([version, meeting])
        session.flush()
        extracted = ExtractedDocument(
            document_version_id=version.id,
            parser_name="stub",
            parser_version="test",
            status="completed",
            extracted_text="",
            page_count=1,
            pages_json="[]",
            citation_map_json="[]",
            updated_at=datetime.utcnow(),
        )
        session.add(extracted)
        session.flush()

        decision = Decision(
            meeting_id=meeting.id,
            source_document_id=document.id,
            decision_number="4/25",
            agenda_item="סעיף 1",
            decision_text="מאשרים החלטת הועדה המקצועית להקצאות קרקע בפרוטוקול מס' 4/25.",
            decision_signature_norm="sig-1",
            parser_confidence=0.9,
            is_public=True,
            metadata_json="{}",
            updated_at=datetime.utcnow(),
        )
        session.add(decision)
        session.flush()

        session.add(
            DecisionCitation(
                decision_id=decision.id,
                document_id=document.id,
                document_version_id=version.id,
                source_type="protocol",
                page_number=2,
                start_offset=400,
                end_offset=460,
                anchor_label="p.2",
                anchor_text=decision.decision_text,
                metadata_json="{}",
            )
        )
        session.add_all(
            [
                _artifact(
                    artifact_id="c1",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    ordinal=8,
                    body_text="כתובת : חטיבת הנגב 45 שכונה :רובע ג'",
                    start_offset=260,
                    end_offset=300,
                    start_page=2,
                    end_page=2,
                    citation_label="p.2",
                ),
                _artifact(
                    artifact_id="c2",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    ordinal=9,
                    body_text="חלקה 94 :מגרש132 : גושים וחלקות: גוש2023",
                    start_offset=301,
                    end_offset=340,
                    start_page=2,
                    end_page=2,
                    citation_label="p.2",
                ),
                _artifact(
                    artifact_id="c3",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    ordinal=10,
                    body_text="מהות הבקשה: בקשה להקצאת קרקע בשטח של 1,800 מ\"ר למטרת הקמת בית כנסת. מורשי חתימה: אלמוני.",
                    start_offset=341,
                    end_offset=399,
                    start_page=2,
                    end_page=2,
                    citation_label="p.2",
                ),
                _artifact(
                    artifact_id="c4",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    ordinal=11,
                    body_text="החלטות: מאשרים החלטת הועדה המקצועית להקצאות קרקע בפרוטוקול מס' 4/25.",
                    start_offset=400,
                    end_offset=460,
                    start_page=2,
                    end_page=2,
                    citation_label="p.2",
                    artifact_kind="decision_unit",
                ),
            ]
        )
        session.commit()

        service = DecisionContextService(session)
        result = service.process_document(
            source_document_id=document.id,
            document_version_id=version.id,
            source_kind="protocol",
        )
        session.commit()

        assert result["contexts"] == 1

        row = session.execute(select(DecisionRequestContext)).scalar_one()
        assert row.request_subject_he is not None
        assert "בקשה להקצאת קרקע" in row.request_subject_he
        assert row.subject_topic_he == "הקצאת קרקע"
        assert row.address_he == "חטיבת הנגב 45"
        assert row.gush == "2023"
        assert row.helka == "94"
        assert row.migrash == "132"


def test_decision_context_service_keeps_non_geographic_context_without_parcel_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "decision_context_non_geo.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()

        document = Document(
            source_site_id=site.id,
            document_external_id="doc:agreement:1",
            canonical_url="https://example.local/agreement.pdf",
            title_he="פרוטוקול ועדת הקצאות מקצועית",
            doc_kind="protocol_full",
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        session.add(document)
        session.flush()

        version = DocumentVersion(
            document_id=document.id,
            sha256="b" * 64,
            byte_size=100,
            storage_uri="agreement.pdf",
            fetched_http_status=200,
            fetched_mime="application/pdf",
        )
        meeting = Meeting(
            source_site_id=site.id,
            meeting_external_id="meeting:2",
            title_he="ישיבת הקצאות",
            committee_name="ועדת הקצאות",
            meeting_kind="committee",
            meeting_code="2-25",
            meeting_date="2025-05-01",
            parse_confidence=0.9,
            metadata_json="{}",
            updated_at=datetime.utcnow(),
        )
        session.add_all([version, meeting])
        session.flush()
        extracted = ExtractedDocument(
            document_version_id=version.id,
            parser_name="stub",
            parser_version="test",
            status="completed",
            extracted_text="",
            page_count=1,
            pages_json="[]",
            citation_map_json="[]",
            updated_at=datetime.utcnow(),
        )
        session.add(extracted)
        session.flush()

        decision = Decision(
            meeting_id=meeting.id,
            source_document_id=document.id,
            decision_number="5/25",
            agenda_item="הסכם רשות",
            decision_text="מאשרים הכנת הסכם רשות.",
            decision_signature_norm="sig-2",
            parser_confidence=0.9,
            is_public=True,
            metadata_json="{}",
            updated_at=datetime.utcnow(),
        )
        session.add(decision)
        session.flush()

        session.add(
            DecisionCitation(
                decision_id=decision.id,
                document_id=document.id,
                document_version_id=version.id,
                source_type="protocol",
                page_number=1,
                start_offset=120,
                end_offset=150,
                anchor_label="p.1",
                anchor_text=decision.decision_text,
                metadata_json="{}",
            )
        )
        session.add_all(
            [
                _artifact(
                    artifact_id="g1",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    ordinal=1,
                    body_text="מהות הבקשה: בקשה להסדרת רשות שימוש ל-4 כיתות גן ילדים בתוך מבנה בית ספר.",
                    start_offset=50,
                    end_offset=119,
                    start_page=1,
                    end_page=1,
                    citation_label="p.1",
                ),
                _artifact(
                    artifact_id="g2",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    ordinal=2,
                    body_text="החלטות: מאשרים הכנת הסכם רשות.",
                    start_offset=120,
                    end_offset=150,
                    start_page=1,
                    end_page=1,
                    citation_label="p.1",
                    artifact_kind="decision_unit",
                ),
            ]
        )
        session.commit()

        service = DecisionContextService(session)
        result = service.process_document(
            source_document_id=document.id,
            document_version_id=version.id,
            source_kind="protocol",
        )
        session.commit()

        assert result["contexts"] == 1
        row = session.execute(select(DecisionRequestContext)).scalar_one()
        assert row.request_subject_he is not None
        assert row.subject_topic_he is not None
        assert row.gush is None
        assert row.helka is None
        assert row.migrash is None


def test_decision_context_service_extracts_subject_without_mahut_label(tmp_path: Path) -> None:
    db_path = tmp_path / "decision_context_inline_subject.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()

        document = Document(
            source_site_id=site.id,
            document_external_id="doc:inline:1",
            canonical_url="https://example.local/inline.pdf",
            title_he="פרוטוקול ועדת הקצאות מקצועית",
            doc_kind="protocol_full",
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        session.add(document)
        session.flush()

        version = DocumentVersion(
            document_id=document.id,
            sha256="c" * 64,
            byte_size=100,
            storage_uri="inline.pdf",
            fetched_http_status=200,
            fetched_mime="application/pdf",
        )
        meeting = Meeting(
            source_site_id=site.id,
            meeting_external_id="meeting:3",
            title_he="ישיבת הקצאות",
            committee_name="ועדת הקצאות",
            meeting_kind="committee",
            meeting_code="3-25",
            meeting_date="2025-05-01",
            parse_confidence=0.9,
            metadata_json="{}",
            updated_at=datetime.utcnow(),
        )
        session.add_all([version, meeting])
        session.flush()
        extracted = ExtractedDocument(
            document_version_id=version.id,
            parser_name="stub",
            parser_version="test",
            status="completed",
            extracted_text="",
            page_count=1,
            pages_json="[]",
            citation_map_json="[]",
            updated_at=datetime.utcnow(),
        )
        session.add(extracted)
        session.flush()

        decision = Decision(
            meeting_id=meeting.id,
            source_document_id=document.id,
            decision_number="9/25",
            agenda_item="סעיף 20",
            decision_text="מאשרים הכנת הסכם רשות לתקופה של 5 שנים.",
            decision_signature_norm="sig-inline",
            parser_confidence=0.9,
            is_public=True,
            metadata_json="{}",
            updated_at=datetime.utcnow(),
        )
        session.add(decision)
        session.flush()

        session.add(
            DecisionCitation(
                decision_id=decision.id,
                document_id=document.id,
                document_version_id=version.id,
                source_type="protocol",
                page_number=20,
                start_offset=220,
                end_offset=280,
                anchor_label="p.20",
                anchor_text=decision.decision_text,
                metadata_json="{}",
            )
        )
        session.add_all(
            [
                _artifact(
                    artifact_id="s1",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    ordinal=10,
                    body_text='העמותה מבקשת להסדיר רשות שימוש לתקופה נוספת ל-6 גני ילדים המופעלים ע"י העמותה מזה שנים.',
                    start_offset=120,
                    end_offset=210,
                    start_page=20,
                    end_page=20,
                    citation_label="p.20",
                ),
                _artifact(
                    artifact_id="s2",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    ordinal=11,
                    body_text="החלטות: מאשרים הכנת הסכם רשות לתקופה של 5 שנים.",
                    start_offset=220,
                    end_offset=280,
                    start_page=20,
                    end_page=20,
                    citation_label="p.20",
                    artifact_kind="decision_unit",
                ),
            ]
        )
        session.commit()

        service = DecisionContextService(session)
        result = service.process_document(
            source_document_id=document.id,
            document_version_id=version.id,
            source_kind="protocol",
        )
        session.commit()

        assert result["contexts"] == 1
        row = session.execute(select(DecisionRequestContext)).scalar_one()
        assert row.request_subject_he == 'העמותה מבקשת להסדיר רשות שימוש לתקופה נוספת ל-6 גני ילדים המופעלים ע"י העמותה מזה שנים'
        assert row.subject_topic_he == "רשות שימוש במבנה"


def test_decision_context_service_prefers_artifacts_when_v2_structure_exists(tmp_path: Path) -> None:
    db_path = tmp_path / "decision_context_artifact.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()

        document = Document(
            source_site_id=site.id,
            document_external_id="doc:artifact:1",
            canonical_url="https://example.local/artifact.pdf",
            title_he="פרוטוקול ועדת הקצאות",
            doc_kind="protocol_full",
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        session.add(document)
        session.flush()

        version = DocumentVersion(
            document_id=document.id,
            sha256="d" * 64,
            byte_size=100,
            storage_uri="artifact.pdf",
            fetched_http_status=200,
            fetched_mime="application/pdf",
        )
        meeting = Meeting(
            source_site_id=site.id,
            meeting_external_id="meeting:artifact",
            title_he="ישיבת הקצאות",
            committee_name="ועדת הקצאות",
            meeting_kind="committee",
            meeting_code="4-25",
            meeting_date="2025-05-02",
            parse_confidence=0.9,
            metadata_json="{}",
            updated_at=datetime.utcnow(),
        )
        session.add_all([version, meeting])
        session.flush()

        extracted = ExtractedDocument(
            document_version_id=version.id,
            parser_name="stub",
            parser_version="test",
            status="completed",
            extracted_text="",
            page_count=1,
            pages_json="[]",
            citation_map_json="[]",
            updated_at=datetime.utcnow(),
        )
        session.add(extracted)
        session.flush()

        decision = Decision(
            meeting_id=meeting.id,
            source_document_id=document.id,
            decision_number="10/25",
            agenda_item="סעיף 7",
            decision_text="מאשרים החלטת הועדה המקצועית להקצאת קרקע.",
            decision_signature_norm="sig-artifact",
            parser_confidence=0.9,
            is_public=True,
            metadata_json="{}",
            updated_at=datetime.utcnow(),
        )
        session.add(decision)
        session.flush()

        session.add(
            DecisionCitation(
                decision_id=decision.id,
                document_id=document.id,
                document_version_id=version.id,
                source_type="protocol",
                page_number=3,
                start_offset=180,
                end_offset=240,
                anchor_label="p.3",
                anchor_text=decision.decision_text,
                metadata_json="{}",
            )
        )
        session.add_all(
            [
                RetrievalArtifact(
                    artifact_id="a1",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    section_id="sec-1",
                    source_kind="protocol",
                    artifact_kind="section_unit",
                    ordinal=1,
                    title_he=document.title_he,
                    committee_name="ועדת הקצאות",
                    meeting_date="2025-05-02",
                    header_path_json=json.dumps([document.title_he, "בקשה להקצאת קרקע"], ensure_ascii=False),
                    body_text="כתובת : חטיבת הנגב 45 חלקה 94 מגרש 132 גוש2023 מהות הבקשה: בקשה להקצאת קרקע לצורך הקמת בית כנסת",
                    retrieval_text="artifact one",
                    retrieval_text_norm="artifact one",
                    start_offset=80,
                    end_offset=179,
                    start_page=3,
                    end_page=3,
                    citation_label="p.3",
                    trigram_count=1,
                    metadata_json="{}",
                ),
                RetrievalArtifact(
                    artifact_id="a2",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    section_id="sec-2",
                    source_kind="protocol",
                    artifact_kind="decision_unit",
                    ordinal=2,
                    title_he=document.title_he,
                    committee_name="ועדת הקצאות",
                    meeting_date="2025-05-02",
                    header_path_json=json.dumps([document.title_he, "החלטה"], ensure_ascii=False),
                    body_text="החלטות: מאשרים החלטת הועדה המקצועית להקצאת קרקע.",
                    retrieval_text="artifact two",
                    retrieval_text_norm="artifact two",
                    start_offset=180,
                    end_offset=240,
                    start_page=3,
                    end_page=3,
                    citation_label="p.3",
                    trigram_count=1,
                    metadata_json="{}",
                ),
            ]
        )
        session.commit()

        service = DecisionContextService(session)
        result = service.process_document(
            source_document_id=document.id,
            document_version_id=version.id,
            source_kind="protocol",
        )
        session.commit()

        assert result["contexts"] == 1
        row = session.execute(select(DecisionRequestContext)).scalar_one()
        metadata = json.loads(row.metadata_json or "{}")
        assert row.request_subject_he is not None
        assert "בקשה להקצאת קרקע" in row.request_subject_he
        assert row.source_artifact_ids_json == '["a1", "a2"]'
        assert metadata["source_artifact_ids"] == ["a1", "a2"]


def _artifact(
    *,
    artifact_id: str,
    document_id: int,
    document_version_id: int,
    extracted_document_id: int,
    ordinal: int,
    body_text: str,
    start_offset: int,
    end_offset: int,
    start_page: int,
    end_page: int,
    citation_label: str,
    artifact_kind: str = "section_unit",
) -> RetrievalArtifact:
    return RetrievalArtifact(
        artifact_id=artifact_id,
        document_id=document_id,
        document_version_id=document_version_id,
        extracted_document_id=extracted_document_id,
        section_id=f"sec-{artifact_id}",
        source_kind="protocol",
        artifact_kind=artifact_kind,
        ordinal=ordinal,
        title_he="פרוטוקול",
        committee_name=None,
        meeting_date=None,
        header_path_json=json.dumps(["פרוטוקול", f"סעיף {ordinal}"], ensure_ascii=False),
        body_text=body_text,
        retrieval_text=body_text,
        retrieval_text_norm=body_text,
        start_offset=start_offset,
        end_offset=end_offset,
        start_page=start_page,
        end_page=end_page,
        citation_label=citation_label,
        trigram_count=1,
        metadata_json="{}",
    )
