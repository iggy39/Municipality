from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import municipality.decisions as decisions_module
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.decisions import DecisionExtractionService
from municipality.migrations import apply_all
from municipality.models import Decision, DecisionExtractionCache, Document, DocumentVersion, SourceSite


PROTOCOL_TEXT = "\n".join(
    [
        "ישיבת מועצה רגילה",
        "סיכום והחלטות",
        "1. אישור תקציב החינוך הוחלט לאשר. בעד/נגד/נמנע: 9/1/0",
    ]
)


class StubFallbackClient:
    def __init__(self, *, configured: bool = True, payloads: list[dict] | None = None) -> None:
        self.configured = configured
        self.payloads = payloads or []
        self.calls: list[tuple[str, int]] = []

    def is_configured(self) -> bool:
        return self.configured

    def extract_decisions(self, *, source_text: str, text_offset: int = 0):
        self.calls.append((source_text, text_offset))
        return list(self.payloads)


def test_decision_extraction_prefers_local_and_persists_cache(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'decision-cache.db'}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        document, version = _seed_protocol_document(session)
        fallback = StubFallbackClient(configured=True)
        service = DecisionExtractionService(
            session,
            fallback_client=fallback,
            cache_enabled=True,
            external_rescue_enabled=False,
        )

        result = service.process_document(
            document=document,
            document_version=version,
            extracted_text=PROTOCOL_TEXT,
            citation_map=[{"start": 0, "end": len(PROTOCOL_TEXT), "page": 1}],
            source_kind="protocol",
        )
        session.commit()

        cache_row = session.execute(select(DecisionExtractionCache)).scalar_one()
        stored_decision = session.execute(select(Decision).where(Decision.source_document_id == document.id)).scalar_one()
        metadata = json.loads(stored_decision.metadata_json or "{}")

        assert result["decisions"] == 1
        assert fallback.calls == []
        assert cache_row.selected_mode == "local_deterministic"
        assert cache_row.candidate_count == 1
        assert metadata["decision_extraction_selected_mode"] == "local_deterministic"
        assert metadata["decision_extraction_from_cache"] is False
        assert metadata["api_model_used"] is False


def test_decision_extraction_uses_cache_before_reparsing(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'decision-cache-hit.db'}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        document, version = _seed_protocol_document(session)
        fallback = StubFallbackClient(configured=True)
        service = DecisionExtractionService(
            session,
            fallback_client=fallback,
            cache_enabled=True,
            external_rescue_enabled=False,
        )
        service.process_document(
            document=document,
            document_version=version,
            extracted_text=PROTOCOL_TEXT,
            citation_map=[{"start": 0, "end": len(PROTOCOL_TEXT), "page": 1}],
            source_kind="protocol",
        )
        session.commit()

        def _fail_parse(_text: str):
            raise AssertionError("local parser should not run on cache hit")

        monkeypatch.setattr(decisions_module, "_parse_decision_candidates", _fail_parse)

        result = service.process_document(
            document=document,
            document_version=version,
            extracted_text=PROTOCOL_TEXT,
            citation_map=[{"start": 0, "end": len(PROTOCOL_TEXT), "page": 1}],
            source_kind="protocol",
        )
        session.commit()

        stored_decision = session.execute(select(Decision).where(Decision.source_document_id == document.id)).scalar_one()
        metadata = json.loads(stored_decision.metadata_json or "{}")

        assert result["decisions"] == 1
        assert fallback.calls == []
        assert metadata["decision_extraction_from_cache"] is True


def test_decision_extraction_uses_external_rescue_only_when_enabled(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'decision-external-rescue.db'}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        document, version = _seed_protocol_document(session)
        fallback = StubFallbackClient(
            configured=True,
            payloads=[
                {
                    "decision_text": "אישור תקציב החינוך הוחלט לאשר. בעד/נגד/נמנע: 9/1/0",
                    "decision_number": "1",
                    "agenda_item": "אישור תקציב החינוך",
                    "confidence": 0.99,
                    "start_offset": PROTOCOL_TEXT.find("אישור תקציב החינוך"),
                    "end_offset": len(PROTOCOL_TEXT),
                    "vote": {"for_count": 9, "against_count": 1, "abstain_count": 0, "unanimous": False},
                }
            ],
        )
        service = DecisionExtractionService(
            session,
            fallback_client=fallback,
            cache_enabled=True,
            external_rescue_enabled=True,
        )

        monkeypatch.setattr(decisions_module, "_parse_decision_candidates", lambda _text: [])

        result = service.process_document(
            document=document,
            document_version=version,
            extracted_text=PROTOCOL_TEXT,
            citation_map=[{"start": 0, "end": len(PROTOCOL_TEXT), "page": 1}],
            source_kind="protocol",
        )
        session.commit()

        cache_row = session.execute(select(DecisionExtractionCache)).scalar_one()
        stored_decision = session.execute(select(Decision).where(Decision.source_document_id == document.id)).scalar_one()
        metadata = json.loads(stored_decision.metadata_json or "{}")

        assert result["decisions"] == 1
        assert len(fallback.calls) == 1
        assert cache_row.selected_mode == "external_rescue"
        assert metadata["api_model_used"] is True
        assert metadata["decision_extraction_selected_mode"] == "external_rescue"


def _seed_protocol_document(session: Session) -> tuple[Document, DocumentVersion]:
    site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
    session.add(site)
    session.flush()

    document = Document(
        source_site_id=site.id,
        document_external_id="doc-decision-cache",
        canonical_url="https://example.local/protocol-cache.pdf",
        title_he="פרוטוקול מועצה",
        doc_kind="protocol_full",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    session.add(document)
    session.flush()

    version = DocumentVersion(
        document_id=document.id,
        sha256="3" * 64,
        byte_size=len(PROTOCOL_TEXT.encode("utf-8")),
        storage_uri="storage/raw/protocol-cache.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add(version)
    session.flush()
    return document, version
