from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import municipality.api as api_module
from municipality.db import Base
from municipality.models import (
    Document,
    DocumentVersion,
    ExtractedDocument,
    RetrievalArtifact,
    SemanticNode,
    SourceSite,
    TopicSubject,
    TopicSubjectQualityReport,
    TopicSubjectRun,
)


def _seed_topic_subject_api_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)

    site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
    session.add(site)
    session.flush()

    document = Document(
        source_site_id=site.id,
        document_external_id="doc:subjects",
        canonical_url="https://example.local/protocol.pdf",
        title_he="פרוטוקול בדיקה",
        doc_kind="protocol_full",
        mime_hint="application/pdf",
    )
    session.add(document)
    session.flush()

    version = DocumentVersion(
        document_id=document.id,
        sha256="a" * 64,
        byte_size=10,
        storage_uri="protocol.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add(version)
    session.flush()

    extracted = ExtractedDocument(
        document_version_id=version.id,
        parser_name="test",
        parser_version="1",
        status="ok",
        extracted_text="בדיקה",
        page_count=1,
    )
    node = SemanticNode(
        source_site_id=site.id,
        node_key_hash="b" * 40,
        node_kind="topic",
        semantic_type="topic",
        pref_label_he="סדר יום ושאילתות",
        pref_label_norm="סדר יום ושאילתות",
        parent_node_id=None,
        depth=1,
        specificity_score=0.8,
        confidence=0.9,
        support_count=3,
        status="active",
    )
    run = TopicSubjectRun(
        municipality_slug="ashdod",
        model_provider="ollama",
        model_name="dicta-test",
        status="completed",
        write_mode=True,
        source_artifact_count=5,
        extraction_count=5,
        candidate_subject_count=4,
        candidate_decision_count=1,
        failed_count=0,
    )
    session.add_all([extracted, node, run])
    session.flush()

    def add_subject(
        index: int,
        root: str,
        child: str,
        *,
        object_he: str,
        is_decision: bool = False,
        topic_relevance: str = "topic_bearing",
        row_role: str = "action_anchor",
        anchor_status: str = "validated_anchor",
        linked_event_id: str | None = None,
        report_status: str = "subject_candidate",
    ) -> None:
        artifact_id = f"artifact-{index}"
        event_id = f"event-{index}"
        session.add(
            RetrievalArtifact(
                artifact_id=artifact_id,
                document_id=document.id,
                document_version_id=version.id,
                extracted_document_id=extracted.id,
                section_id=None,
                source_kind="pdf_first_v4_protocol",
                artifact_kind="pdf_first_v4_retrieval_chunk",
                ordinal=index,
                title_he="פרוטוקול בדיקה",
                committee_name=None,
                meeting_date="2026-01-01",
                header_path_json="[]",
                body_text=object_he,
                retrieval_text=object_he,
                retrieval_text_norm=object_he,
                start_offset=0,
                end_offset=len(object_he),
                start_page=1,
                end_page=1,
                citation_label="p.1",
                trigram_count=1,
            )
        )
        session.flush()
        session.add(
            TopicSubject(
                run_id=run.id,
                municipality_slug="ashdod",
                artifact_id=artifact_id,
                semantic_node_id=node.id,
                subject_index=0,
                root_topic_id="topic-root",
                child_topic_id="topic-child",
                topic_label_he="סדר יום ושאילתות",
                source_kind="pdf_first_v4_protocol",
                source_document_id=document.id,
                source_document_version_id=version.id,
                source_ordinal=index,
                source_page_start=1,
                source_page_end=1,
                source_title="פרוטוקול בדיקה",
                subject_root_label_he=root,
                subject_root_label_norm=root,
                subject_child_label_he=child,
                subject_child_label_norm=child,
                subject_object_he=object_he,
                subject_details_he=None,
                artifact_role="action_anchor",
                topic_relevance=topic_relevance,
                event_id=event_id,
                event_topic_label_he="סדר יום ושאילתות",
                row_role=row_role,
                anchor_status=anchor_status,
                linked_event_id=linked_event_id,
                link_confidence=None,
                link_reason=None,
                subject_summary_he=object_he,
                what_text_is_about_he=object_he,
                subject_status="candidate",
                is_decision=is_decision,
                decision_label_he="התקבלה" if is_decision else None,
                decision_label_norm="התקבלה" if is_decision else None,
                decision_summary_he="אושר" if is_decision else None,
                decision_source_quote_he="אושר" if is_decision else None,
                confidence=0.9,
                validation_status="accepted",
                failure_reason=None,
            )
        )
        session.add(
            TopicSubjectQualityReport(
                run_id=run.id,
                artifact_id=artifact_id,
                semantic_node_id=node.id,
                topic_label_he="סדר יום ושאילתות",
                real_text=f"טקסט מקור מלא עבור {object_he}",
                what_text_is_about_he=object_he,
                artifact_role=row_role,
                topic_relevance=topic_relevance,
                event_id=event_id,
                event_topic_label_he="סדר יום ושאילתות",
                row_role=row_role,
                anchor_status=anchor_status,
                linked_event_id=linked_event_id,
                link_confidence=0.8 if linked_event_id else None,
                link_reason="פירוט שייך לעוגן" if linked_event_id else None,
                subject_root_by_dicta=root,
                subject_child_by_dicta=child,
                subject_object_by_dicta=object_he,
                subject_details_by_dicta="פרטי בדיקה",
                decision_by_dicta="התקבלה" if is_decision else None,
                my_judgment=report_status,
                ground_truth=f"דעתי: הנושא צריך להיות {root}{' / ' + child if child else ''} עבור {object_he}",
                reason_for_failure="",
                status=report_status,
                metadata_json=None,
            )
        )

    add_subject(1, "בקשה", "בקשת מידע", object_he="שאילתת ניקיון")
    add_subject(2, "דיווח", "", object_he="דיווח מלחים")
    add_subject(3, "דיווח", "סקירה", object_he="סקירת מוכנות", topic_relevance="topic_suspect")
    add_subject(4, "אישור החלטה", "אישור החלטת ועדה", object_he="אישור ועדה", is_decision=True)
    add_subject(
        5,
        "בקשה",
        "בקשת מידע",
        object_he="שורת פירוט",
        row_role="dependent_detail",
        anchor_status="linked_to_validated_anchor",
        linked_event_id="event-1",
        report_status="linked_detail",
    )
    session.commit()
    return session


def test_topic_subject_groups_apply_display_mapping_without_rewriting_stored_labels() -> None:
    session = _seed_topic_subject_api_session()
    try:
        payload = api_module.topic_subject_groups(db=session)
    finally:
        session.close()

    assert payload["count"] == 4
    groups = {group["group_key"]: group for group in payload["groups"]}
    assert groups["request_information"]["label_he"] == "שאילתות מועצה"
    assert groups["request_information"]["stored_labels"] == {"בקשה / בקשת מידע": 1}
    assert groups["report_review"]["count"] == 2
    assert groups["report_review"]["stored_labels"] == {"דיווח": 1, "דיווח / סקירה": 1}
    assert groups["report_review"]["topic_suspect_count"] == 1
    assert groups["decision_approval"]["decision_count"] == 1


def test_topic_subject_items_can_filter_by_display_group_key() -> None:
    session = _seed_topic_subject_api_session()
    try:
        payload = api_module.topic_subject_items(db=session, display_group="report_review")
    finally:
        session.close()

    assert payload["total_count"] == 2
    assert [item["display"]["group_label_he"] for item in payload["items"]] == ["דיווחים וסקירות", "דיווחים וסקירות"]
    assert [item["stored"]["label_he"] for item in payload["items"]] == ["דיווח", "דיווח / סקירה"]
    assert payload["items"][1]["quality"]["flags"] == ["topic_suspect"]
    assert payload["items"][0]["audit"]["full_source_text_he"] == "טקסט מקור מלא עבור דיווח מלחים"
    assert payload["items"][0]["audit"]["agent_subject_prediction_en"] == "Report about דיווח מלחים"
    assert payload["items"][0]["audit"]["judge_prediction"] == {
        "root_label_he": "דיווח",
        "child_label_he": "",
        "object_he": "דיווח מלחים",
        "note_he": "דעתי: הנושא צריך להיות דיווח עבור דיווח מלחים",
        "judgment": "subject_candidate",
    }
    assert payload["items"][1]["audit"]["ground_truth_subject_he"] == "דעתי: הנושא צריך להיות דיווח / סקירה עבור סקירת מוכנות"
    assert payload["items"][1]["audit"]["agent_subject_prediction_en"] == "Review report about סקירת מוכנות"
    assert payload["items"][1]["audit"]["model_prediction"]["child_label_he"] == "סקירה"


def test_topic_subject_items_prefer_v2_action_subject_matter_fields() -> None:
    session = _seed_topic_subject_api_session()
    try:
        subject = session.query(TopicSubject).filter_by(artifact_id="artifact-1").one()
        quality = session.query(TopicSubjectQualityReport).filter_by(artifact_id="artifact-1").one()
        subject.action_root_label_he = "שאילתה"
        subject.action_child_label_he = ""
        subject.subject_matter_he = "שאלת ניקיון מדויקת"
        subject.action_details_he = "פרטי פעולה חדשים"
        quality.action_root_by_dicta = "שאילתה"
        quality.action_child_by_dicta = ""
        quality.subject_matter_by_dicta = "שאלת ניקיון מדויקת"
        quality.action_details_by_dicta = "פרטי פעולה חדשים"
        session.commit()

        payload = api_module.topic_subject_items(db=session, display_group="council_inquiry")
    finally:
        session.close()

    item = payload["items"][0]
    assert item["stored"]["root_label_he"] == "שאילתה"
    assert item["stored"]["child_label_he"] == ""
    assert item["stored"]["label_he"] == "שאילתה"
    assert item["subject"]["object_he"] == "שאלת ניקיון מדויקת"
    assert item["subject"]["details_he"] == "פרטי פעולה חדשים"
    assert item["audit"]["agent_subject_prediction_en"] == "Council inquiry about שאלת ניקיון מדויקת"
    assert item["audit"]["model_prediction"]["root_label_he"] == "שאילתה"
    assert item["audit"]["model_prediction"]["object_he"] == "שאלת ניקיון מדויקת"
    assert item["audit"]["model_prediction"]["details_he"] == "פרטי פעולה חדשים"


def test_topic_subject_items_include_linked_detail_context_for_anchor() -> None:
    session = _seed_topic_subject_api_session()
    try:
        payload = api_module.topic_subject_items(db=session, display_group="request_information")
    finally:
        session.close()

    assert payload["total_count"] == 1
    item = payload["items"][0]
    assert item["stored"]["child_label_he"] == "בקשת מידע"
    assert item["audit"]["agent_subject_prediction_en"] == "Council inquiry about שאילתת ניקיון"
    assert item["linked_details"][0]["full_source_text_he"] == "טקסט מקור מלא עבור שורת פירוט"
    assert item["linked_details"][0]["ground_truth_subject_he"] == "דעתי: הנושא צריך להיות בקשה / בקשת מידע עבור שורת פירוט"
    assert item["linked_details"][0]["agent_subject_prediction_en"] == "Council inquiry about שורת פירוט"


def test_subject_browser_page_wires_subject_api_endpoints() -> None:
    response = api_module.subject_browser_page()
    body = bytes(response.body).decode("utf-8")

    assert "Municipal Subject Browser" in body
    assert 'data-groups-endpoint="/api/topic-subjects/groups"' in body
    assert 'data-items-endpoint="/api/topic-subjects"' in body
    assert "Source topic may be inaccurate" in body
    assert "Judged action + subject matter" in body
    assert "Correct action (judge)" in body
    assert "Correct action: root" in body
    assert "Correct action: child" in body
    assert "Correct subject matter" in body
    assert "Upstream retrieval/topic grouping for the source chunk" in body
    assert "My judged procedural action after checking the source" in body
    assert "Raw model output" in body
    assert "Full source text" in body
    assert "Your prediction" not in body
    assert "Subject object" not in body
    assert "Subject child" not in body
    assert "Municipality" in body
    assert "Tel Aviv" in body
    assert "groupsCollapseButton" in body
    assert "Collapse" in body
    assert "Show row" in body
    assert "Document full path" in body
    assert "Raw model action: root" in body
    assert "Raw model action: child" in body
    assert "Raw model subject matter" in body
    assert "Raw model details" in body
    assert "Linked detail rows" in body


def test_subject_browser_route_returns_html() -> None:
    client = TestClient(api_module.app)
    response = client.get("/ui/subjects")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Municipal Subject Browser" in response.text
