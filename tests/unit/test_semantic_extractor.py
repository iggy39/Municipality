from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from municipality.migrations import apply_all
from municipality.models import Document, DocumentVersion, SourceSite
from municipality.semantic_extractor import (
    DEFAULT_DICTALM_MODEL,
    OllamaSemanticClient,
    SemanticExtractor,
    SemanticModelResponse,
    build_semantic_model_client,
    _extract_response_content,
    _parse_json_content,
)


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


def test_parse_json_content_accepts_markdown_fenced_object() -> None:
    content = """```json
    {
      \"evidence_spans\": [],
      \"nodes\": []
    }
    ```"""
    parsed = _parse_json_content(content)

    assert isinstance(parsed, dict)
    assert parsed.get("nodes") == []


def test_parse_json_content_extracts_object_from_prefixed_text() -> None:
    content = "model output:\n{\"evidence_spans\":[],\"nodes\":[]}\nend"
    parsed = _parse_json_content(content)

    assert isinstance(parsed, dict)
    assert parsed.get("evidence_spans") == []


def test_extract_response_content_handles_content_parts_array() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "{\"evidence_spans\":[]"},
                        {"type": "text", "text": ',\"nodes\":[]}'},
                    ]
                }
            }
        ]
    }

    extracted = _extract_response_content(payload)
    parsed = _parse_json_content(extracted)

    assert isinstance(extracted, str)
    assert isinstance(parsed, dict)
    assert parsed.get("nodes") == []


def test_build_semantic_model_client_ignores_non_dictalm_provider_env(monkeypatch) -> None:
    monkeypatch.setenv("SEMANTIC_MODEL_PROVIDER", "ai21")
    monkeypatch.delenv("SEMANTIC_MODEL", raising=False)

    client = build_semantic_model_client()

    assert isinstance(client, OllamaSemanticClient)
    assert client.model_name == DEFAULT_DICTALM_MODEL


def test_build_semantic_model_client_defaults_to_dictalm_ollama(monkeypatch) -> None:
    monkeypatch.delenv("SEMANTIC_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("SEMANTIC_MODEL", raising=False)

    client = build_semantic_model_client()

    assert isinstance(client, OllamaSemanticClient)
    assert client.model_name == DEFAULT_DICTALM_MODEL


def test_parse_json_content_coerces_single_evidence_span_object() -> None:
    content = (
        "debug prefix "
        '{"span_id":"s1","category":"decision","start_offset":0,'
        '"end_offset":10,"text":"החלטה"}'
    )
    parsed = _parse_json_content(content)

    assert isinstance(parsed, dict)
    assert isinstance(parsed.get("evidence_spans"), list)
    assert isinstance(parsed.get("nodes"), list)
    assert parsed["evidence_spans"][0]["span_id"] == "s1"
