from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.extraction import PdfExtractionResult, parse_extracted_text, score_extraction_quality
from municipality.migrations import apply_all
from municipality.models import (
    ArtifactSemanticLink,
    Document,
    DocumentVersion,
    ExtractedDocument,
    PipelineRunStep,
    RetrievalArtifact,
    SemanticAlias,
    SemanticCandidateReject,
    SemanticDocumentRun,
    SemanticMention,
    SemanticNode,
    SourceSite,
)
from municipality.processing import ProcessingService, SemanticEnrichmentPolicy
from municipality.semantic_extractor import SemanticExtractor, SemanticModelResponse
from municipality.semantic_service import SemanticService


HE_PROTOCOL_TEXT = (
    "סיכום והחלטות\n"
    "הוחלט לאשר תקציב תחבורה עירונית לשנת 2026.\n"
    "יוקם צוות מעקב לביצוע התוכנית."
)


class StubExtractor:
    def __init__(self, by_uri: dict[str, str]):
        self.by_uri = by_uri

    def extract(self, payload: bytes) -> PdfExtractionResult:
        uri = payload.decode("utf-8")
        raw_text = self.by_uri[uri]
        full_text, pages, citation_map = parse_extracted_text(raw_text)
        quality_score, quality_flags, quality_summary = score_extraction_quality(full_text, pages)
        return PdfExtractionResult(
            ok=True,
            parser_name="stub",
            parser_version="test",
            full_text=full_text,
            pages=pages,
            citation_map=citation_map,
            quality_score=quality_score,
            quality_flags=quality_flags,
            quality_summary=quality_summary,
            error_code=None,
            warning_text=None,
        )


class NoopDecisionExtractionService:
    def process_document(self, **_kwargs) -> dict[str, int]:
        return {"meeting_id": 0, "decisions": 0}


class StubSemanticClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "StubProvider"

    @property
    def model_name(self) -> str:
        return "stub-semantic-v1"

    def is_configured(self) -> bool:
        return True

    def extract_semantic(self, *, request_payload: dict) -> SemanticModelResponse:
        self.calls += 1
        return SemanticModelResponse(
            payload=self.payload,
            request_tokens=101,
            response_tokens=44,
            error_code=None,
            error_text=None,
        )


class FailingSemanticClient:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "StubProvider"

    @property
    def model_name(self) -> str:
        return "stub-semantic-fail"

    def is_configured(self) -> bool:
        return True

    def extract_semantic(self, *, request_payload: dict) -> SemanticModelResponse:
        self.calls += 1
        return SemanticModelResponse(
            payload=None,
            request_tokens=12,
            response_tokens=None,
            error_code="MODEL_REQUEST_FAILED",
            error_text="stubbed semantic failure",
        )


def test_m4_processing_persists_semantic_artifacts_and_rerun_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "m4_semantic_processing.db"
    storage_root = tmp_path / "raw"
    storage_root.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()

        document = Document(
            source_site_id=site.id,
            document_external_id="doc:m4:semantic",
            canonical_url="https://example.local/m4-semantic.pdf",
            title_he="פרוטוקול ועדת תחבורה",
            doc_kind="protocol_full",
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        session.add(document)
        session.flush()

        version = DocumentVersion(
            document_id=document.id,
            sha256="1" * 64,
            byte_size=12,
            storage_uri="tree/ashdod/m4-semantic.pdf",
            fetched_http_status=200,
            fetched_mime="application/pdf",
        )
        session.add(version)
        session.commit()

        (storage_root / "tree/ashdod").mkdir(parents=True, exist_ok=True)
        (storage_root / version.storage_uri).write_bytes(version.storage_uri.encode("utf-8"))

        semantic_payload = _semantic_payload_for_text(parse_extracted_text(HE_PROTOCOL_TEXT)[0])
        semantic_client = StubSemanticClient(payload=semantic_payload)
        semantic_service = SemanticService(
            session,
            extractor=SemanticExtractor(session, model_client=semantic_client),
        )
        processor = ProcessingService(
            session=session,
            storage_root=storage_root,
            extractor=StubExtractor({version.storage_uri: HE_PROTOCOL_TEXT}),
            decision_extraction=NoopDecisionExtractionService(),
            semantic_service=semantic_service,
            semantic_policy=SemanticEnrichmentPolicy(min_text_chars=0),
        )

        processor.run()
        processor.run()

        extracted_rows = session.execute(select(ExtractedDocument)).scalars().all()
        artifact_rows = session.execute(select(RetrievalArtifact)).scalars().all()
        semantic_runs = session.execute(select(SemanticDocumentRun)).scalars().all()
        semantic_nodes = session.execute(select(SemanticNode)).scalars().all()
        semantic_aliases = session.execute(select(SemanticAlias)).scalars().all()
        semantic_mentions = session.execute(select(SemanticMention)).scalars().all()
        artifact_links = session.execute(select(ArtifactSemanticLink)).scalars().all()
        reject_rows = session.execute(select(SemanticCandidateReject)).scalars().all()
        semantic_steps = session.execute(
            select(PipelineRunStep).where(PipelineRunStep.step_name == "semantic_enrichment")
        ).scalars().all()

        assert len(extracted_rows) == 1
        assert artifact_rows
        assert len(semantic_runs) == 1
        assert semantic_runs[0].status == "completed"
        assert semantic_runs[0].api_call_count == 1
        assert semantic_client.calls == 1
        assert len(semantic_nodes) == 1
        assert len(semantic_aliases) >= 1
        assert len(semantic_mentions) == 1
        assert len(artifact_links) >= 1
        assert len(reject_rows) >= 1
        assert len(semantic_steps) == 2
        assert all(step.status == "completed" for step in semantic_steps)


def test_m4_processing_keeps_extraction_and_artifacts_when_semantic_fails(tmp_path: Path) -> None:
    db_path = tmp_path / "m4_semantic_failure.db"
    storage_root = tmp_path / "raw"
    storage_root.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()

        document = Document(
            source_site_id=site.id,
            document_external_id="doc:m4:semantic-fail",
            canonical_url="https://example.local/m4-semantic-fail.pdf",
            title_he="פרוטוקול ועדת תחבורה",
            doc_kind="protocol_full",
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        session.add(document)
        session.flush()

        version = DocumentVersion(
            document_id=document.id,
            sha256="2" * 64,
            byte_size=12,
            storage_uri="tree/ashdod/m4-semantic-fail.pdf",
            fetched_http_status=200,
            fetched_mime="application/pdf",
        )
        session.add(version)
        session.commit()

        (storage_root / "tree/ashdod").mkdir(parents=True, exist_ok=True)
        (storage_root / version.storage_uri).write_bytes(version.storage_uri.encode("utf-8"))

        failing_client = FailingSemanticClient()
        semantic_service = SemanticService(
            session,
            extractor=SemanticExtractor(session, model_client=failing_client),
        )
        processor = ProcessingService(
            session=session,
            storage_root=storage_root,
            extractor=StubExtractor({version.storage_uri: HE_PROTOCOL_TEXT}),
            decision_extraction=NoopDecisionExtractionService(),
            semantic_service=semantic_service,
            semantic_policy=SemanticEnrichmentPolicy(min_text_chars=0),
        )

        processor.run()

        extracted_rows = session.execute(select(ExtractedDocument)).scalars().all()
        artifact_rows = session.execute(select(RetrievalArtifact)).scalars().all()
        semantic_run = session.execute(select(SemanticDocumentRun)).scalar_one()
        extract_step = session.execute(
            select(PipelineRunStep).where(PipelineRunStep.step_name == "extract_document")
        ).scalar_one()
        semantic_step = session.execute(
            select(PipelineRunStep).where(PipelineRunStep.step_name == "semantic_enrichment")
        ).scalar_one()

        assert extracted_rows
        assert artifact_rows
        assert semantic_run.status == "failed"
        assert semantic_run.error_code == "MODEL_REQUEST_FAILED"
        assert failing_client.calls == 1
        assert extract_step.status == "completed"
        assert semantic_step.status == "failed"


def _semantic_payload_for_text(full_text: str) -> dict:
    sentence_text = "הוחלט לאשר תקציב תחבורה עירונית לשנת 2026."
    sentence_start = full_text.index(sentence_text)
    sentence_end = sentence_start + len(sentence_text)
    mention_text = "תחבורה עירונית"
    mention_start = full_text.index(mention_text)
    mention_end = mention_start + len(mention_text)

    return {
        "evidence_spans": [
            {
                "span_id": "s-decision-1",
                "category": "decision",
                "start_offset": sentence_start,
                "end_offset": sentence_end,
                "text": sentence_text,
                "confidence": 0.84,
                "regex_boost": 0.08,
                "hint_terms": ["הוחלט", "תקציב"],
            }
        ],
        "nodes": [
            {
                "candidate_id": "n-transport",
                "label_he": "תחבורה עירונית",
                "node_kind": "topic",
                "semantic_type": "transport_program",
                "confidence": 0.9,
                "mentions": [
                    {
                        "mention_text": mention_text,
                        "start_offset": mention_start,
                        "end_offset": mention_end,
                        "confidence": 0.88,
                    }
                ],
                "aliases": [
                    {
                        "alias_label_he": "מערך תחבורה עירונית",
                        "alias_kind": "surface",
                        "confidence": 0.7,
                    }
                ],
                "evidence_span_ids": ["s-decision-1"],
            }
        ],
        "rejects": [
            {
                "candidate_label_he": "כללי",
                "candidate_label_norm": "כללי",
                "node_kind": "topic",
                "semantic_type": "generic",
                "reason_code": "TOO_GENERIC",
                "model_confidence": 0.2,
                "metadata": {"source": "stub"},
            }
        ],
    }
