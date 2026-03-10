from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.chunking import build_chunks
from municipality.eval_semantic import evaluate_semantic_eval_set, load_semantic_eval_set
from municipality.extraction import parse_extracted_text
from municipality.migrations import apply_all
from municipality.models import (
    ChunkSemanticLink,
    Document,
    DocumentVersion,
    ExtractedDocument,
    SemanticAlias,
    SemanticCandidateReject,
    SemanticDocumentRun,
    SemanticMention,
    SemanticNode,
    SourceSite,
    TextChunk,
)
from municipality.search import SearchService


HE_TRANSPORT_TEXT = "הוחלט לקדם פרויקט תחבורה עירונית מרכזית בעיר."
HE_CLEAN_TEXT = "הוחלט לקדם פרויקט ניקיון שכונתי מתמשך בעיר."


def test_m4_semantic_eval_harness_meets_thresholds(tmp_path: Path) -> None:
    db_path = tmp_path / "m4_semantic_eval.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        search_service = SearchService(session)
        _seed_eval_fixture(session=session, search_service=search_service)

        eval_set_path = tmp_path / "m4_eval_set.json"
        eval_set_path.write_text(
            json.dumps(
                {
                    "retrieval_cases": [
                        {
                            "case_id": "m4-r01",
                            "query": "פרויקט תחבורה",
                            "top_k": 5,
                            "semantic_mode": "boost",
                            "expected_document_title_contains": "תחבורה",
                            "expected_source_type": "protocol",
                            "expected_semantic_nodes": [{"label": "תחבורה עירונית", "aliases": ["פרויקט תחבורה"]}],
                            "expected_evidence": [{"source_kind": "protocol"}],
                            "expected_min_semantic_match_count": 1,
                        },
                        {
                            "case_id": "m4-r02",
                            "query": "ניקיון שכונתי",
                            "top_k": 5,
                            "semantic_mode": "filter",
                            "semantic_label": "ניקיון שכונתי",
                            "expected_document_title_contains": "ניקיון",
                            "expected_source_type": "protocol",
                            "expected_semantic_nodes": [{"label": "ניקיון שכונתי", "aliases": []}],
                            "expected_evidence": [{"source_kind": "protocol"}],
                            "expected_min_semantic_match_count": 1,
                        },
                    ],
                    "negative_cases": [
                        {
                            "case_id": "m4-n01",
                            "query": "כללי",
                            "expected_reject_reason": "TOO_GENERIC",
                            "disallowed_node_labels": ["כללי"],
                        },
                        {
                            "case_id": "m4-n02",
                            "query": "12345",
                            "expected_reject_reason": "NUMERIC_ONLY_LABEL",
                            "disallowed_node_labels": ["12345"],
                        },
                    ],
                    "run_quality_thresholds": {
                        "one_call_compliance_min": 1.0,
                        "evidence_backed_active_nodes_min": 1.0,
                        "semantic_hit_rate_min": 1.0,
                        "retrieval_lift_min": 0.0,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        eval_set = load_semantic_eval_set(eval_set_path)
        summary, case_results = evaluate_semantic_eval_set(
            search_service=search_service,
            session=session,
            eval_set=eval_set,
        )

        assert case_results
        assert summary.total_retrieval_cases == 2
        assert summary.semantic_hit_rate == 1.0
        assert summary.one_call_compliance_rate == 1.0
        assert summary.evidence_backed_active_node_rate == 1.0
        assert summary.duplicate_active_node_rate == 0.0
        assert summary.negative_reject_hit_rate == 1.0
        assert summary.thresholds_passed


def _seed_eval_fixture(*, session: Session, search_service: SearchService) -> None:
    site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
    session.add(site)
    session.flush()

    transport_doc = Document(
        source_site_id=site.id,
        document_external_id="doc:transport",
        canonical_url="https://example.local/transport.pdf",
        title_he="פרוטוקול תחבורה",
        doc_kind="protocol_full",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    clean_doc = Document(
        source_site_id=site.id,
        document_external_id="doc:clean",
        canonical_url="https://example.local/clean.pdf",
        title_he="פרוטוקול ניקיון",
        doc_kind="protocol_full",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    session.add_all([transport_doc, clean_doc])
    session.flush()

    transport_ver = DocumentVersion(
        document_id=transport_doc.id,
        sha256="6" * 64,
        byte_size=16,
        storage_uri="tree/ashdod/transport-eval.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    clean_ver = DocumentVersion(
        document_id=clean_doc.id,
        sha256="7" * 64,
        byte_size=16,
        storage_uri="tree/ashdod/clean-eval.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add_all([transport_ver, clean_ver])
    session.flush()

    transport_extracted_id, transport_text = _insert_extracted_doc(
        session,
        document_version_id=transport_ver.id,
        raw_text=HE_TRANSPORT_TEXT,
    )
    clean_extracted_id, clean_text = _insert_extracted_doc(
        session,
        document_version_id=clean_ver.id,
        raw_text=HE_CLEAN_TEXT,
    )

    _index_text(
        search_service,
        document_id=transport_doc.id,
        document_version_id=transport_ver.id,
        extracted_document_id=transport_extracted_id,
        full_text=transport_text,
    )
    _index_text(
        search_service,
        document_id=clean_doc.id,
        document_version_id=clean_ver.id,
        extracted_document_id=clean_extracted_id,
        full_text=clean_text,
    )

    transport_chunk = session.execute(
        select(TextChunk).where(TextChunk.document_version_id == transport_ver.id)
    ).scalar_one()
    clean_chunk = session.execute(
        select(TextChunk).where(TextChunk.document_version_id == clean_ver.id)
    ).scalar_one()

    transport_node = SemanticNode(
        source_site_id=site.id,
        node_key_hash="a1" * 20,
        node_kind="topic",
        semantic_type="transport_program",
        pref_label_he="תחבורה עירונית",
        pref_label_norm="תחבורה עירונית",
        parent_node_id=None,
        depth=0,
        specificity_score=0.87,
        confidence=0.9,
        support_count=1,
        status="active",
        first_seen_document_version_id=transport_ver.id,
        last_seen_document_version_id=transport_ver.id,
        metadata_json=None,
        updated_at=datetime.utcnow(),
    )
    clean_node = SemanticNode(
        source_site_id=site.id,
        node_key_hash="b2" * 20,
        node_kind="topic",
        semantic_type="sanitation_program",
        pref_label_he="ניקיון שכונתי",
        pref_label_norm="ניקיון שכונתי",
        parent_node_id=None,
        depth=0,
        specificity_score=0.79,
        confidence=0.84,
        support_count=1,
        status="active",
        first_seen_document_version_id=clean_ver.id,
        last_seen_document_version_id=clean_ver.id,
        metadata_json=None,
        updated_at=datetime.utcnow(),
    )
    session.add_all([transport_node, clean_node])
    session.flush()

    session.add(
        SemanticAlias(
            semantic_node_id=transport_node.id,
            alias_hash="c3" * 20,
            alias_label_he="פרויקט תחבורה",
            alias_label_norm="פרויקט תחבורה",
            alias_kind="surface",
            confidence=0.72,
            first_seen_document_version_id=transport_ver.id,
            last_seen_document_version_id=transport_ver.id,
            metadata_json=None,
        )
    )

    session.add_all(
        [
            ChunkSemanticLink(
                chunk_id=transport_chunk.chunk_id,
                semantic_node_id=transport_node.id,
                confidence=0.95,
                source_mention_id=None,
                metadata_json=None,
            ),
            ChunkSemanticLink(
                chunk_id=clean_chunk.chunk_id,
                semantic_node_id=clean_node.id,
                confidence=0.91,
                source_mention_id=None,
                metadata_json=None,
            ),
        ]
    )

    _insert_mention(
        session,
        node=transport_node,
        document_id=transport_doc.id,
        document_version_id=transport_ver.id,
        full_text=transport_text,
        mention_text="תחבורה עירונית",
    )
    _insert_mention(
        session,
        node=clean_node,
        document_id=clean_doc.id,
        document_version_id=clean_ver.id,
        full_text=clean_text,
        mention_text="ניקיון שכונתי",
    )

    run = SemanticDocumentRun(
        document_version_id=transport_ver.id,
        prompt_hash="m4-eval-prompt",
        model_provider="StubProvider",
        model_name="stub-semantic-v1",
        status="completed",
        api_call_count=1,
        request_tokens=120,
        response_tokens=48,
        error_code=None,
        error_text=None,
        extraction_payload_json=json.dumps({"nodes": [], "evidence_spans": []}),
        validation_report_json=json.dumps({"is_valid": True, "issues": []}),
        canonicalization_report_json=json.dumps({"accepted_nodes": [1, 2], "rejected_nodes": [3], "warnings": []}),
        started_at=datetime.utcnow(),
        finished_at=datetime.utcnow(),
    )
    session.add(run)
    session.flush()

    session.add_all(
        [
            SemanticCandidateReject(
                semantic_document_run_id=run.id,
                candidate_label_he="כללי",
                candidate_label_norm="כללי",
                node_kind="topic",
                semantic_type="generic",
                reason_code="TOO_GENERIC",
                model_confidence=0.2,
                metadata_json=None,
            ),
            SemanticCandidateReject(
                semantic_document_run_id=run.id,
                candidate_label_he="12345",
                candidate_label_norm="12345",
                node_kind="topic",
                semantic_type="generic",
                reason_code="NUMERIC_ONLY_LABEL",
                model_confidence=0.1,
                metadata_json=None,
            ),
        ]
    )

    session.commit()


def _insert_extracted_doc(session: Session, *, document_version_id: int, raw_text: str) -> tuple[int, str]:
    full_text, pages, citation_map = parse_extracted_text(raw_text)
    row = ExtractedDocument(
        document_version_id=document_version_id,
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
    session.add(row)
    session.flush()
    return row.id, full_text


def _index_text(
    search_service: SearchService,
    *,
    document_id: int,
    document_version_id: int,
    extracted_document_id: int,
    full_text: str,
) -> None:
    _normalized_text, _pages, citation_map = parse_extracted_text(full_text)
    chunks = build_chunks(
        document_version_id=document_version_id,
        text=full_text,
        citation_map=citation_map,
        source_kind="protocol",
    )
    search_service.replace_document_chunks(
        document_id=document_id,
        document_version_id=document_version_id,
        extracted_document_id=extracted_document_id,
        source_kind="protocol",
        chunks=chunks,
    )


def _insert_mention(
    session: Session,
    *,
    node: SemanticNode,
    document_id: int,
    document_version_id: int,
    full_text: str,
    mention_text: str,
) -> None:
    start = full_text.index(mention_text)
    end = start + len(mention_text)
    session.add(
        SemanticMention(
            semantic_node_id=node.id,
            document_id=document_id,
            document_version_id=document_version_id,
            source_kind="protocol",
            start_offset=start,
            end_offset=end,
            start_page=1,
            end_page=1,
            mention_text=mention_text,
            mention_text_norm=mention_text,
            mention_confidence=0.9,
            evidence_hash=f"{node.id:040d}"[-40:],
            metadata_json=None,
        )
    )
