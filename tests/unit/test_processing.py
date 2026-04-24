from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.extraction import ExtractedPage, PdfExtractionResult
from municipality.migrations import apply_all
from municipality.models import Document, DocumentVersion, PipelineRunStep, SourceSite
from municipality.processing import ProcessingService, SemanticEnrichmentPolicy
from municipality.semantic_service import SemanticServiceResult


LONG_TEXT = ("נושא תקציב וחינוך עירוני. " * 30).strip()


class StubExtractor:
    def extract(self, pdf_bytes: bytes) -> PdfExtractionResult:
        assert pdf_bytes == b"pdf-bytes"
        return PdfExtractionResult(
            ok=True,
            parser_name="stub",
            parser_version="1",
            full_text=LONG_TEXT,
            pages=[ExtractedPage(page=1, text=LONG_TEXT, start_offset=0, end_offset=len(LONG_TEXT))],
            citation_map=[{"start": 0, "end": len(LONG_TEXT), "page": 1}],
            quality_score=0.95,
            quality_flags=[],
            quality_summary={"total_chars": len(LONG_TEXT)},
            error_code=None,
            warning_text=None,
        )


class StubDecisionExtractionService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def process_document(self, **kwargs) -> None:
        self.calls.append(str(kwargs.get("source_kind")))


class StubDecisionContextService:
    def process_document(self, **kwargs) -> dict[str, int]:
        return {"contexts": 0}


class StubChunkEmbeddingService:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def delete_chunk_embeddings(self, *, chunk_ids: list[str]) -> None:
        return None

    def index_chunks(self, *, chunks: list[dict]) -> object:
        self.calls.append(len(chunks))
        return type("EmbeddingResult", (), {"enabled": True, "created": len(chunks), "cached": 0, "total": len(chunks), "error_text": None})()


class StubSemanticService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run_for_document(self, **kwargs) -> SemanticServiceResult:
        self.calls.append(str(kwargs.get("source_kind")))
        return SemanticServiceResult(
            run_id=11,
            status="completed",
            from_cache=False,
            api_call_count=1,
            evidence_spans=2,
            node_candidates=2,
            accepted_nodes=1,
            rejected_nodes=0,
            validation_issues=0,
            aliases=1,
            mentions=1,
            edges=0,
            decision_links=0,
            artifact_links=1,
            reject_rows=0,
        )


def test_processing_skips_semantic_enrichment_for_attachments_by_default(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'processing-attachment.db'}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        doc_id = _seed_pdf_document(session, storage_root=tmp_path, doc_kind="attachment", suffix="attachment")
        semantic_service = StubSemanticService()
        embedding_service = StubChunkEmbeddingService()
        service = ProcessingService(
            session=session,
            storage_root=tmp_path,
            extractor=StubExtractor(),
            decision_extraction=StubDecisionExtractionService(),
            decision_context_service=StubDecisionContextService(),
            chunk_embedding_service=embedding_service,
            semantic_service=semantic_service,
            semantic_policy=SemanticEnrichmentPolicy(),
        )

        service.run(doc_id=doc_id)

        steps = session.execute(select(PipelineRunStep.step_name, PipelineRunStep.status, PipelineRunStep.detail)).all()
        by_name = {name: (status, detail) for name, status, detail in steps}

        assert by_name["structure_artifact_index"][0] == "completed"
        assert by_name["semantic_enrichment"][0] == "skipped"
        assert by_name["semantic_enrichment"][1] == "SKIP_SOURCE_KIND:attachment"
        assert semantic_service.calls == []
        assert embedding_service.calls


def test_processing_runs_semantic_enrichment_for_protocol_documents(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'processing-protocol.db'}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        doc_id = _seed_pdf_document(session, storage_root=tmp_path, doc_kind="protocol_full", suffix="protocol")
        semantic_service = StubSemanticService()
        service = ProcessingService(
            session=session,
            storage_root=tmp_path,
            extractor=StubExtractor(),
            decision_extraction=StubDecisionExtractionService(),
            decision_context_service=StubDecisionContextService(),
            chunk_embedding_service=StubChunkEmbeddingService(),
            semantic_service=semantic_service,
            semantic_policy=SemanticEnrichmentPolicy(),
        )

        service.run(doc_id=doc_id)

        steps = session.execute(select(PipelineRunStep.step_name, PipelineRunStep.status, PipelineRunStep.detail)).all()
        by_name = {name: (status, detail) for name, status, detail in steps}

        assert by_name["semantic_enrichment"][0] == "completed"
        assert "run_id=11" in by_name["semantic_enrichment"][1]
        assert semantic_service.calls == ["protocol"]


def _seed_pdf_document(session: Session, *, storage_root: Path, doc_kind: str, suffix: str) -> int:
    storage_root.mkdir(parents=True, exist_ok=True)
    storage_path = storage_root / f"{suffix}.pdf"
    storage_path.write_bytes(b"pdf-bytes")

    site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
    session.add(site)
    session.flush()

    document = Document(
        source_site_id=site.id,
        document_external_id=f"doc-{suffix}",
        canonical_url=f"https://example.local/{suffix}.pdf",
        title_he=f"מסמך {suffix}",
        doc_kind=doc_kind,
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    session.add(document)
    session.flush()

    session.add(
        DocumentVersion(
            document_id=document.id,
            sha256=(suffix[:1] or "x") * 64,
            byte_size=9,
            storage_uri=storage_path.name,
            fetched_http_status=200,
            fetched_mime="application/pdf",
        )
    )
    session.commit()
    return document.id
