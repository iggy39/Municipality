from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from municipality.migrations import apply_all
from municipality.models import Document, DocumentVersion, SourceSite
from municipality.semantic_extractor import SemanticExtractor, SemanticModelResponse


class StubSemanticClient:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "StubProvider"

    @property
    def model_name(self) -> str:
        return "stub-model"

    def is_configured(self) -> bool:
        return True

    def extract_semantic(self, *, request_payload: dict) -> SemanticModelResponse:
        self.calls += 1
        return SemanticModelResponse(
            payload={"evidence_spans": [], "nodes": []},
            request_tokens=12,
            response_tokens=5,
            error_code=None,
            error_text=None,
        )


def test_semantic_extractor_enforces_one_call_per_cached_tuple(tmp_path: Path) -> None:
    db_path = tmp_path / "semantic.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
        session.add(site)
        session.flush()
        document = Document(
            source_site_id=site.id,
            document_external_id="doc-sem-1",
            canonical_url="https://example.local/protocol-sem.pdf",
            title_he="פרוטוקול",
            doc_kind="protocol_full",
            mime_hint="application/pdf",
            last_seen_at=datetime.utcnow(),
        )
        session.add(document)
        session.flush()
        version = DocumentVersion(
            document_id=document.id,
            sha256="9" * 64,
            byte_size=11,
            storage_uri="tree/ashdod/protocol-sem.pdf",
            fetched_http_status=200,
            fetched_mime="application/pdf",
        )
        session.add(version)
        session.commit()

        client = StubSemanticClient()
        extractor = SemanticExtractor(session, model_client=client)
        payload = {"system_instruction": "x", "evidence_spans": [], "nodes": []}

        first = extractor.extract_once(
            document_version_id=version.id,
            prompt_hash="abc123",
            request_payload=payload,
        )
        session.commit()

        second = extractor.extract_once(
            document_version_id=version.id,
            prompt_hash="abc123",
            request_payload=payload,
        )

        assert first.from_cache is False
        assert first.run.api_call_count == 1
        assert second.from_cache is True
        assert second.run.id == first.run.id
        assert client.calls == 1
