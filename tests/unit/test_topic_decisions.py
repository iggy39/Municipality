from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.migrations import apply_all
from municipality.models import (
    ArtifactSemanticLink,
    Document,
    DocumentVersion,
    ExtractedDocument,
    RetrievalArtifact,
    SemanticNode,
    SourceSite,
    TopicDecision,
    TopicDecisionQualityReport,
    TopicDecisionRun,
)
from municipality.topic_decisions import (
    TopicDecisionArtifact,
    TopicDecisionResearchConfig,
    load_accepted_topic_artifacts,
    process_topic_decision_payload,
    quote_supported_by_text,
    run_topic_decision_research,
)


REAL_TEXT = "סעיף 1: חברי המועצה מאשרים פה אחד את ההסכם. ההחלטה התקבלה בדיון."


class StubTopicDecisionClient:
    def extract(self, *, artifact: TopicDecisionArtifact, config: TopicDecisionResearchConfig) -> dict[str, Any]:
        return {
            "decisions": [
                {
                    "title_he": artifact.topic_label_he,
                    "decision_text_he": "חברי המועצה מאשרים פה אחד את ההסכם",
                    "summary_he": "אישור ההסכם פה אחד",
                    "decision_presence": {"code": "DECISION_PRESENT", "label_he": "החלטה או פעולה קיימת בטקסט", "confidence_label": "גבוהה"},
                    "decision_event_type": {"code": "ACCEPTANCE", "label_he": "קבלה או אישור", "confidence_label": "גבוהה"},
                    "decision_kind": {"code": "APPROVAL", "label_he": "אישור", "confidence_label": "גבוהה"},
                    "outcome_status": {"code": "APPROVED", "label_he": "אושר", "confidence_label": "גבוהה"},
                    "legal_effect": {"code": "BINDING", "label_he": "מחייב", "confidence_label": "בינונית"},
                    "primary_time": {"kind": "unknown", "start": None, "end": None, "precision": "unknown", "label_he": "תאריך לא ידוע", "confidence_label": "נמוכה"},
                    "time_anchors": [],
                    "source_quote_he": "חברי המועצה מאשרים פה אחד את ההסכם",
                    "confidence": 0.91,
                    "new_decision_label_candidate_he": None,
                    "limitations": [],
                }
            ],
            "no_decision_reason_he": None,
            "rationale_he": "הציטוט כולל אישור מפורש",
        }


def test_quote_supported_by_text_accepts_exact_and_token_grounding() -> None:
    assert quote_supported_by_text(quote="חברי המועצה מאשרים פה אחד את ההסכם", text=REAL_TEXT) is True
    assert quote_supported_by_text(quote="טקסט שאינו מופיע בהחלטה", text=REAL_TEXT) is False


def test_process_topic_decision_payload_rejects_ungrounded_quote() -> None:
    artifact = _artifact_dataclass(real_text=REAL_TEXT)
    decisions, quality = process_topic_decision_payload(
        artifact=artifact,
        model_payload={
            "decisions": [
                {
                    "title_he": "הסכם",
                    "decision_text_he": "אישור הסכם",
                    "summary_he": "אישור הסכם",
                    "decision_presence": {"code": "DECISION_PRESENT", "label_he": "החלטה או פעולה קיימת בטקסט"},
                    "decision_event_type": {"code": "ACCEPTANCE", "label_he": "קבלה או אישור"},
                    "decision_kind": {"code": "APPROVAL", "label_he": "אישור"},
                    "outcome_status": {"code": "APPROVED", "label_he": "אושר"},
                    "legal_effect": {"code": "BINDING", "label_he": "מחייב"},
                    "primary_time": {"kind": "unknown"},
                    "time_anchors": [],
                    "source_quote_he": "ציטוט שלא נמצא במקור",
                    "confidence": 0.8,
                    "limitations": [],
                }
            ]
        },
    )

    assert len(decisions) == 1
    assert decisions[0].validation_status == "failed"
    assert quality.status == "failed"
    assert "source_quote_not_grounded_in_artifact_text" in quality.reason_for_failure


def test_process_topic_decision_payload_rejects_request_only_approval() -> None:
    artifact = _artifact_dataclass(real_text="לאור האמור אבקש לאשר את ההתקשרות עם הספק.")
    decisions, quality = process_topic_decision_payload(
        artifact=artifact,
        model_payload={
            "decisions": [
                {
                    "title_he": "התקשרות עם ספק",
                    "decision_text_he": "אישור התקשרות עם ספק",
                    "summary_he": "אישור התקשרות",
                    "decision_presence": {"code": "REQUEST_ONLY", "label_he": "בקשה או הצעה בלבד"},
                    "decision_event_type": {"code": "PROPOSAL", "label_he": "הצעה או בקשה שהועלתה"},
                    "decision_kind": {"code": "APPROVAL", "label_he": "אישור"},
                    "outcome_status": {"code": "APPROVED", "label_he": "אושר"},
                    "legal_effect": {"code": "BINDING", "label_he": "מחייב"},
                    "primary_time": {"kind": "unknown"},
                    "time_anchors": [],
                    "source_quote_he": "לאור האמור אבקש לאשר את ההתקשרות עם הספק",
                    "confidence": 0.8,
                    "new_decision_label_candidate_he": None,
                    "limitations": [],
                }
            ]
        },
    )

    assert len(decisions) == 1
    assert decisions[0].validation_status == "failed"
    assert quality.status == "failed"
    assert "decision_presence_not_decision:REQUEST_ONLY" in quality.reason_for_failure
    assert "approval_without_approval_language" in quality.reason_for_failure


def test_run_topic_decision_research_persists_separate_research_rows(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'topic-decisions.db'}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        _seed_topic_artifact(session)
        result = run_topic_decision_research(
            session,
            config=TopicDecisionResearchConfig(municipality_slug="ashdod", write=True),
            client=StubTopicDecisionClient(),
        )

        run = session.execute(select(TopicDecisionRun)).scalar_one()
        decision = session.execute(select(TopicDecision)).scalar_one()
        quality = session.execute(select(TopicDecisionQualityReport)).scalar_one()

        assert result.run_id == run.id
        assert run.source_artifact_count == 1
        assert run.accepted_decision_count == 1
        assert decision.artifact_id == "artifact-1"
        assert decision.validation_status == "accepted"
        assert decision.decision_kind_code == "APPROVAL"
        assert quality.status == "accepted"


def test_load_accepted_topic_artifacts_uses_topic_tree_links(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'topic-artifacts.db'}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        node = _seed_topic_artifact(session)
        artifacts = load_accepted_topic_artifacts(session, municipality_slug="ashdod")

        assert len(artifacts) == 1
        assert artifacts[0].artifact_id == "artifact-1"
        assert artifacts[0].semantic_node_id == node.id
        assert artifacts[0].topic_label_he == "הסכמי פיתוח"
        assert artifacts[0].root_label_he == "הסכמים והתקשרויות"


def test_load_accepted_topic_artifacts_adds_adjacent_context(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'topic-artifacts-context.db'}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        _seed_topic_artifact(session, include_neighbors=True)
        artifacts = load_accepted_topic_artifacts(session, municipality_slug="ashdod")

        assert len(artifacts) == 1
        assert [context["relation"] for context in artifacts[0].neighbor_contexts] == ["previous_protocol_row", "next_protocol_row"]
        assert "previous_protocol_row" in artifacts[0].decision_context_text
        assert "next_protocol_row" in artifacts[0].decision_context_text


def _seed_topic_artifact(session: Session, *, include_neighbors: bool = False) -> SemanticNode:
    site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
    session.add(site)
    session.flush()

    document = Document(
        source_site_id=site.id,
        document_external_id="doc-topic-decision",
        canonical_url="https://example.local/doc.pdf",
        title_he="פרוטוקול מועצה",
        doc_kind="protocol_full",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    session.add(document)
    session.flush()

    version = DocumentVersion(
        document_id=document.id,
        sha256="4" * 64,
        byte_size=len(REAL_TEXT.encode("utf-8")),
        storage_uri="storage/raw/doc.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add(version)
    session.flush()

    extracted = ExtractedDocument(
        document_version_id=version.id,
        parser_name="test",
        parser_version="test",
        status="completed",
        extracted_text=REAL_TEXT,
        page_count=1,
    )
    session.add(extracted)
    session.flush()

    root = SemanticNode(
        source_site_id=site.id,
        node_key_hash="root-hash",
        node_kind="topic",
        semantic_type="pdf_first_v4_topic_root",
        pref_label_he="הסכמים והתקשרויות",
        pref_label_norm="הסכמים והתקשרויות",
        parent_node_id=None,
        depth=0,
        specificity_score=0.5,
        confidence=1.0,
        support_count=1,
        status="active",
        first_seen_document_version_id=version.id,
        last_seen_document_version_id=version.id,
        metadata_json=json.dumps({"root_topic_id": "root_agreements"}, ensure_ascii=False),
    )
    session.add(root)
    session.flush()

    child = SemanticNode(
        source_site_id=site.id,
        node_key_hash="child-hash",
        node_kind="topic",
        semantic_type="pdf_first_v4_topic_child",
        pref_label_he="הסכמי פיתוח",
        pref_label_norm="הסכמי פיתוח",
        parent_node_id=root.id,
        depth=1,
        specificity_score=0.78,
        confidence=0.9,
        support_count=1,
        status="active",
        first_seen_document_version_id=version.id,
        last_seen_document_version_id=version.id,
        metadata_json=json.dumps({"root_topic_id": "root_agreements", "child_topic_id": "child_agreements"}, ensure_ascii=False),
    )
    session.add(child)
    session.flush()

    artifact = RetrievalArtifact(
        artifact_id="artifact-1",
        document_id=document.id,
        document_version_id=version.id,
        extracted_document_id=extracted.id,
        section_id="section-1",
        source_kind="pdf_first_v4_protocol",
        artifact_kind="pdf_first_v4_retrieval_chunk",
        ordinal=1,
        title_he="הסכמי פיתוח",
        committee_name=None,
        meeting_date=None,
        header_path_json=json.dumps(["הסכמים והתקשרויות", "הסכמי פיתוח"], ensure_ascii=False),
        body_text=REAL_TEXT,
        retrieval_text=REAL_TEXT,
        retrieval_text_norm=REAL_TEXT,
        start_offset=0,
        end_offset=len(REAL_TEXT),
        start_page=1,
        end_page=1,
        citation_label="p.1",
        trigram_count=0,
        metadata_json=json.dumps({"decision_candidate_id": "dc_test"}, ensure_ascii=False),
    )
    session.add(artifact)
    session.flush()

    if include_neighbors:
        session.add_all(
            [
                RetrievalArtifact(
                    artifact_id="artifact-prev",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    section_id="section-prev",
                    source_kind="pdf_first_v4_protocol",
                    artifact_kind="pdf_first_v4_retrieval_chunk",
                    ordinal=0,
                    title_he="רקע",
                    committee_name=None,
                    meeting_date=None,
                    header_path_json=json.dumps(["רקע"], ensure_ascii=False),
                    body_text="רקע כללי לפני הסעיף.",
                    retrieval_text="רקע כללי לפני הסעיף.",
                    retrieval_text_norm="רקע כללי לפני הסעיף.",
                    start_offset=0,
                    end_offset=20,
                    start_page=1,
                    end_page=1,
                    citation_label="p.1",
                    trigram_count=0,
                    metadata_json=json.dumps({}, ensure_ascii=False),
                ),
                RetrievalArtifact(
                    artifact_id="artifact-next",
                    document_id=document.id,
                    document_version_id=version.id,
                    extracted_document_id=extracted.id,
                    section_id="section-next",
                    source_kind="pdf_first_v4_protocol",
                    artifact_kind="pdf_first_v4_retrieval_chunk",
                    ordinal=2,
                    title_he="המשך",
                    committee_name=None,
                    meeting_date=None,
                    header_path_json=json.dumps(["המשך"], ensure_ascii=False),
                    body_text="המשך הדיון לאחר הסעיף.",
                    retrieval_text="המשך הדיון לאחר הסעיף.",
                    retrieval_text_norm="המשך הדיון לאחר הסעיף.",
                    start_offset=21,
                    end_offset=45,
                    start_page=1,
                    end_page=1,
                    citation_label="p.1",
                    trigram_count=0,
                    metadata_json=json.dumps({}, ensure_ascii=False),
                ),
            ]
        )
        session.flush()

    session.add(
        ArtifactSemanticLink(
            artifact_id=artifact.artifact_id,
            semantic_node_id=child.id,
            confidence=0.9,
            source_mention_id=None,
            metadata_json=json.dumps({"root_topic_id": "root_agreements", "child_topic_id": "child_agreements"}, ensure_ascii=False),
        )
    )
    session.flush()
    return child


def _artifact_dataclass(*, real_text: str) -> TopicDecisionArtifact:
    return TopicDecisionArtifact(
        artifact_id="artifact-test",
        semantic_node_id=1,
        topic_label_he="בדיקה",
        root_topic_id="root_test",
        root_label_he="שורש",
        child_topic_id=None,
        child_label_he=None,
        source_kind="pdf_first_v4_protocol",
        source_document_id=1,
        source_document_version_id=1,
        source_ordinal=1,
        source_title="מקור בדיקה",
        source_url="https://example.local/doc.pdf",
        artifact_kind="pdf_first_v4_retrieval_chunk",
        start_page=1,
        end_page=1,
        header_path=["שורש"],
        real_text=real_text,
        retrieval_text=real_text,
        topic_confidence=0.9,
        existing_decision_candidate_id=None,
        metadata={},
    )
