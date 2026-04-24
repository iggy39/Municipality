from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.api import search as search_endpoint
from municipality.api import semantic_node_detail, semantic_runs, semantic_tree
from municipality.chunking import build_chunks
from municipality.extraction import parse_extracted_text
from municipality.migrations import apply_all
from municipality.models import (
    ArtifactSemanticLink,
    Decision,
    DecisionSemanticLink,
    Document,
    DocumentVersion,
    ExtractedDocument,
    Meeting,
    RetrievalArtifact,
    SemanticAlias,
    SemanticCandidateReject,
    SemanticDocumentRun,
    SemanticMention,
    SemanticNode,
    SourceSite,
)
from municipality.search import SearchService


HE_DOC_TEXT = "הוחלט לקדם פרויקט תחבורה עירונית מרכזית בעיר."


def test_m4_semantic_api_endpoints_expose_search_tree_node_and_runs(tmp_path: Path) -> None:
    db_path = tmp_path / "m4_api.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        fixture = _seed_semantic_api_fixture(session)

        search_payload = search_endpoint(
            q="פרויקט",
            semantic_node_id=fixture["child_node_id"],
            semantic_mode="filter",
            include_semantic_debug=True,
            db=session,
        )
        assert search_payload["count"] == 1
        result = search_payload["results"][0]
        assert result["semantic_match_count"] >= 1
        assert result["semantic_boost"] > 0.0
        assert result["semantic_nodes"][0]["id"] == fixture["child_node_id"]

        root_search_payload = search_endpoint(
            q="פרויקט",
            semantic_node_id=fixture["root_node_id"],
            semantic_mode="filter",
            include_semantic_debug=True,
            db=session,
        )
        assert root_search_payload["count"] == 1
        root_result = root_search_payload["results"][0]
        assert root_result["semantic_match_count"] >= 1
        assert fixture["child_node_id"] in root_result["semantic_node_ids"]

        tree_payload = semantic_tree(muni="ashdod", db=session)
        assert any(item["id"] == fixture["root_node_id"] for item in tree_payload["items"])

        subtree_payload = semantic_tree(root_id=fixture["root_node_id"], depth=1, db=session)
        assert {item["id"] for item in subtree_payload["items"]} == {
            fixture["root_node_id"],
            fixture["child_node_id"],
        }

        node_payload = semantic_node_detail(fixture["child_node_id"], db=session)
        assert node_payload["aliases"]
        assert node_payload["linked_decisions"]
        assert node_payload["linked_artifacts"]
        assert node_payload["mentions"]

        runs_payload = semantic_runs(document_version_id=fixture["document_version_id"], db=session)
        assert runs_payload["count"] == 1
        run = runs_payload["runs"][0]
        assert run["api_call_count"] == 1
        assert run["validation"]["issue_count"] == 0
        assert run["canonicalization"]["accepted_nodes"] == 1
        assert run["reject_reason_histogram"].get("TOO_GENERIC") == 1


def _seed_semantic_api_fixture(session: Session) -> dict[str, int]:
    site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
    session.add(site)
    session.flush()

    meeting = Meeting(
        source_site_id=site.id,
        meeting_external_id="meeting:2026-03-01",
        title_he="ישיבת מועצה 01.03.2026",
        committee_name="ישיבות מועצה",
        meeting_kind="רגילה",
        meeting_code="12/26",
        meeting_date="2026-03-01",
        parse_confidence=0.95,
        metadata_json=json.dumps({"seed": True}),
        updated_at=datetime.utcnow(),
    )
    session.add(meeting)
    session.flush()

    document = Document(
        source_site_id=site.id,
        document_external_id="doc:m4:api",
        canonical_url="https://example.local/m4-api.pdf",
        title_he="פרוטוקול ועדת תחבורה",
        doc_kind="protocol_full",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    session.add(document)
    session.flush()

    version = DocumentVersion(
        document_id=document.id,
        sha256="5" * 64,
        byte_size=14,
        storage_uri="tree/ashdod/m4-api.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add(version)
    session.flush()

    full_text, pages, citation_map = parse_extracted_text(HE_DOC_TEXT)
    extracted = ExtractedDocument(
        document_version_id=version.id,
        parser_name="stub",
        parser_version="test",
        status="completed",
        extracted_text=full_text,
        page_count=len(pages),
        pages_json=json.dumps(
            [
                {
                    "page": page.page,
                    "text": page.text,
                    "start_offset": page.start_offset,
                    "end_offset": page.end_offset,
                }
                for page in pages
            ],
            ensure_ascii=False,
        ),
        citation_map_json=json.dumps(citation_map),
        quality_score=1.0,
        quality_flags_json=json.dumps([]),
        quality_summary_json=json.dumps({}),
        error_code=None,
        warning_text=None,
        updated_at=datetime.utcnow(),
    )
    session.add(extracted)
    session.flush()

    search_service = SearchService(session)
    chunks = build_chunks(
        document_version_id=version.id,
        text=full_text,
        citation_map=citation_map,
        source_kind="protocol",
    )
    search_service.replace_document_chunks(
        document_id=document.id,
        document_version_id=version.id,
        extracted_document_id=extracted.id,
        source_kind="protocol",
        chunks=chunks,
    )

    chunk = session.execute(select(RetrievalArtifact).where(RetrievalArtifact.document_version_id == version.id)).scalar_one()

    decision = Decision(
        meeting_id=meeting.id,
        source_document_id=document.id,
        decision_number="1",
        agenda_item="תחבורה",
        decision_text="אישור פרויקט תחבורה עירונית",
        decision_signature_norm="אישור פרויקט תחבורה עירונית",
        parser_confidence=0.94,
        is_public=True,
        metadata_json=json.dumps({"seed": True}),
        updated_at=datetime.utcnow(),
    )
    session.add(decision)
    session.flush()

    root_node = SemanticNode(
        source_site_id=site.id,
        node_key_hash="d" * 40,
        node_kind="topic",
        semantic_type="transport_domain",
        pref_label_he="תחבורה",
        pref_label_norm="תחבורה",
        parent_node_id=None,
        depth=0,
        specificity_score=0.8,
        confidence=0.88,
        support_count=2,
        status="active",
        first_seen_document_version_id=version.id,
        last_seen_document_version_id=version.id,
        metadata_json=None,
        updated_at=datetime.utcnow(),
    )
    session.add(root_node)
    session.flush()

    child_node = SemanticNode(
        source_site_id=site.id,
        node_key_hash="e" * 40,
        node_kind="topic",
        semantic_type="transport_program",
        pref_label_he="תחבורה עירונית",
        pref_label_norm="תחבורה עירונית",
        parent_node_id=root_node.id,
        depth=1,
        specificity_score=0.9,
        confidence=0.93,
        support_count=1,
        status="active",
        first_seen_document_version_id=version.id,
        last_seen_document_version_id=version.id,
        metadata_json=None,
        updated_at=datetime.utcnow(),
    )
    session.add(child_node)
    session.flush()

    session.add(
        SemanticAlias(
            semantic_node_id=child_node.id,
            alias_hash="f" * 40,
            alias_label_he="פרויקט תחבורה",
            alias_label_norm="פרויקט תחבורה",
            alias_kind="surface",
            confidence=0.7,
            first_seen_document_version_id=version.id,
            last_seen_document_version_id=version.id,
            metadata_json=None,
        )
    )

    mention_text = "תחבורה עירונית"
    mention_start = full_text.index(mention_text)
    mention_end = mention_start + len(mention_text)
    mention = SemanticMention(
        semantic_node_id=child_node.id,
        document_id=document.id,
        document_version_id=version.id,
        source_kind="protocol",
        start_offset=mention_start,
        end_offset=mention_end,
        start_page=1,
        end_page=1,
        mention_text=mention_text,
        mention_text_norm=mention_text,
        mention_confidence=0.9,
        evidence_hash="1" * 40,
        metadata_json=json.dumps({"seed": True}),
    )
    session.add(mention)
    session.flush()

    session.add(
        ArtifactSemanticLink(
            artifact_id=chunk.artifact_id,
            semantic_node_id=child_node.id,
            confidence=0.95,
            source_mention_id=mention.id,
            metadata_json=json.dumps({"seed": True}),
        )
    )
    session.add(
        DecisionSemanticLink(
            decision_id=decision.id,
            semantic_node_id=child_node.id,
            relation_role="subject",
            confidence=0.92,
            source_mention_id=mention.id,
            metadata_json=json.dumps({"seed": True}),
        )
    )

    semantic_run = SemanticDocumentRun(
        document_version_id=version.id,
        prompt_hash="9" * 64,
        model_provider="StubProvider",
        model_name="stub-semantic-v1",
        status="completed",
        api_call_count=1,
        request_tokens=100,
        response_tokens=55,
        error_code=None,
        error_text=None,
        extraction_payload_json=json.dumps({"evidence_spans": [], "nodes": []}),
        validation_report_json=json.dumps({"is_valid": True, "issues": []}),
        canonicalization_report_json=json.dumps(
            {
                "accepted_nodes": [{"candidate_id": "n-transport"}],
                "rejected_nodes": [{"candidate_id": "n-generic"}],
                "warnings": [],
            },
            ensure_ascii=False,
        ),
        started_at=datetime.utcnow(),
        finished_at=datetime.utcnow(),
    )
    session.add(semantic_run)
    session.flush()

    session.add(
        SemanticCandidateReject(
            semantic_document_run_id=semantic_run.id,
            candidate_label_he="כללי",
            candidate_label_norm="כללי",
            node_kind="topic",
            semantic_type="generic",
            reason_code="TOO_GENERIC",
            model_confidence=0.2,
            metadata_json=json.dumps({"seed": True}),
        )
    )

    session.commit()
    return {
        "document_version_id": version.id,
        "root_node_id": root_node.id,
        "child_node_id": child_node.id,
    }
