from __future__ import annotations

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
    SourceSite,
    TextChunk,
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
                TextChunk(
                    chunk_id="c1",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    source_kind="protocol",
                    chunk_index=8,
                    chunk_text="כתובת : חטיבת הנגב 45 שכונה :רובע ג'",
                    chunk_text_norm="",
                    start_offset=260,
                    end_offset=300,
                    start_page=2,
                    end_page=2,
                    citation_label="p.2",
                    trigram_count=1,
                ),
                TextChunk(
                    chunk_id="c2",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    source_kind="protocol",
                    chunk_index=9,
                    chunk_text="חלקה 94 :מגרש132 : גושים וחלקות: גוש2023",
                    chunk_text_norm="",
                    start_offset=301,
                    end_offset=340,
                    start_page=2,
                    end_page=2,
                    citation_label="p.2",
                    trigram_count=1,
                ),
                TextChunk(
                    chunk_id="c3",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    source_kind="protocol",
                    chunk_index=10,
                    chunk_text="מהות הבקשה: בקשה להקצאת קרקע בשטח של 1,800 מ\"ר למטרת הקמת בית כנסת. מורשי חתימה: אלמוני.",
                    chunk_text_norm="",
                    start_offset=341,
                    end_offset=399,
                    start_page=2,
                    end_page=2,
                    citation_label="p.2",
                    trigram_count=1,
                ),
                TextChunk(
                    chunk_id="c4",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    source_kind="protocol",
                    chunk_index=11,
                    chunk_text="החלטות: מאשרים החלטת הועדה המקצועית להקצאות קרקע בפרוטוקול מס' 4/25.",
                    chunk_text_norm="",
                    start_offset=400,
                    end_offset=460,
                    start_page=2,
                    end_page=2,
                    citation_label="p.2",
                    trigram_count=1,
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
                TextChunk(
                    chunk_id="g1",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    source_kind="protocol",
                    chunk_index=1,
                    chunk_text="מהות הבקשה: בקשה להסדרת רשות שימוש ל-4 כיתות גן ילדים בתוך מבנה בית ספר.",
                    chunk_text_norm="",
                    start_offset=50,
                    end_offset=119,
                    start_page=1,
                    end_page=1,
                    citation_label="p.1",
                    trigram_count=1,
                ),
                TextChunk(
                    chunk_id="g2",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    source_kind="protocol",
                    chunk_index=2,
                    chunk_text="החלטות: מאשרים הכנת הסכם רשות.",
                    chunk_text_norm="",
                    start_offset=120,
                    end_offset=150,
                    start_page=1,
                    end_page=1,
                    citation_label="p.1",
                    trigram_count=1,
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
                TextChunk(
                    chunk_id="s1",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    source_kind="protocol",
                    chunk_index=10,
                    chunk_text='העמותה מבקשת להסדיר רשות שימוש לתקופה נוספת ל-6 גני ילדים המופעלים ע"י העמותה מזה שנים.',
                    chunk_text_norm="",
                    start_offset=120,
                    end_offset=210,
                    start_page=20,
                    end_page=20,
                    citation_label="p.20",
                    trigram_count=1,
                ),
                TextChunk(
                    chunk_id="s2",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    source_kind="protocol",
                    chunk_index=11,
                    chunk_text="החלטות: מאשרים הכנת הסכם רשות לתקופה של 5 שנים.",
                    chunk_text_norm="",
                    start_offset=220,
                    end_offset=280,
                    start_page=20,
                    end_page=20,
                    citation_label="p.20",
                    trigram_count=1,
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
