from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from municipality.artifact_embeddings import ArtifactEmbeddingService
from municipality.artifact_search import ArtifactSearchService
from municipality.migrations import apply_all
from municipality.models import Document, DocumentVersion, ExtractedDocument, RetrievalArtifact, SourceSite
from municipality.rag_arch import RagArchitectureConfig
from municipality.rag_backend import build_embedding_backend, build_search_backend
def test_rag_arch_defaults_to_v2() -> None:
    config = RagArchitectureConfig.from_env({})

    assert config.version == "v2"
    assert config.uses_v2 is True
    assert config.v2_index_build_enabled is True


def test_rag_backend_uses_artifact_services_immediately(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'rag-backend.db'}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        assert isinstance(build_search_backend(session=session), ArtifactSearchService)
        assert isinstance(build_embedding_backend(session=session), ArtifactEmbeddingService)

        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()

        document = Document(
            source_site_id=site.id,
            document_external_id="doc:v2:1",
            canonical_url="https://example.local/doc-v2.pdf",
            title_he="פרוטוקול ועדת חינוך",
            doc_kind="protocol_full",
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        session.add(document)
        session.flush()

        document_version = DocumentVersion(
            document_id=document.id,
            sha256="1" * 64,
            byte_size=10,
            storage_uri="tree/ashdod/doc-v2.pdf",
            fetched_http_status=200,
            fetched_mime="application/pdf",
        )
        session.add(document_version)
        session.flush()

        extracted = ExtractedDocument(
            document_version_id=document_version.id,
            parser_name="stub",
            parser_version="1",
            status="completed",
            extracted_text="פרוטוקול ועדת חינוך",
            page_count=1,
            citation_map_json='[{"start":0,"end":20,"page":1}]',
        )
        session.add(extracted)
        session.flush()

        artifact = RetrievalArtifact(
            artifact_id="artifact-1",
            document_id=document.id,
            document_version_id=document_version.id,
            extracted_document_id=extracted.id,
            section_id=None,
            source_kind="protocol",
            artifact_kind="document_profile",
            ordinal=0,
            title_he=document.title_he,
            committee_name="ועדת חינוך",
            meeting_date="2026-04-23",
            header_path_json='["פרוטוקול ועדת חינוך"]',
            body_text="פרוטוקול ועדת חינוך",
            retrieval_text="פרוטוקול ועדת חינוך",
            retrieval_text_norm="פרוטוקול ועדת חינוך",
            start_offset=0,
            end_offset=20,
            start_page=1,
            end_page=1,
            citation_label="p.1",
            trigram_count=1,
            metadata_json="{}",
        )
        session.add(artifact)
        session.commit()

        assert isinstance(build_search_backend(session=session), ArtifactSearchService)
        assert isinstance(build_embedding_backend(session=session), ArtifactEmbeddingService)
