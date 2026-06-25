from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.chunking import normalize_for_search
from municipality.migrations import apply_all
from municipality.models import (
    ArtifactSemanticLink,
    ArtifactTopicAnnotation,
    Document,
    DocumentVersion,
    ExtractedDocument,
    RetrievalArtifact,
    SemanticNode,
    SourceSite,
)
from municipality.pdf_first_semantic_bridge import BridgeConfig, CleanTopic, build_pdf_first_semantic_topic_tree


class StubCleaner:
    def clean(self, candidates):
        out = {}
        for candidate in candidates:
            if normalize_for_search(candidate.label) == normalize_for_search("א לפקודת העיריות לאיחוד וחלוקה ושינויים במקרקעין בבעלות הרשות המקומית מחליטים פה אחד"):
                out[candidate.norm] = CleanTopic(
                    input_norm=candidate.norm,
                    canonical_label=None,
                    root_label=None,
                    child_label=None,
                    confidence=0.0,
                    route="stub_reject",
                    reject_reason="ocr_sentence_fragment",
                )
            elif "מינוי מועצה דתית" in candidate.label:
                out[candidate.norm] = CleanTopic(
                    input_norm=candidate.norm,
                    canonical_label="מינוי מועצה דתית",
                    root_label="דת ושירותי דת",
                    child_label="מינוי מועצה דתית",
                    confidence=0.91,
                    route="stub_accept",
                )
        return out, {"enabled": True, "applied": True, "model": "stub-dictalm"}


def test_pdf_first_semantic_bridge_builds_nodes_links_and_rejects_noise(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'bridge.db'}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        _seed_pdf_first_topic_rows(session)
        result = build_pdf_first_semantic_topic_tree(
            session,
            config=BridgeConfig(municipality_slug="ashdod", write=True, llm_cleanup=True),
            cleaner=StubCleaner(),
        )

        assert result.mode == "write"
        assert result.llm_cleanup["applied"] is True
        assert result.link_count == 2
        assert result.rejected_label_count >= 1

        nodes = session.execute(select(SemanticNode).order_by(SemanticNode.depth, SemanticNode.pref_label_he)).scalars().all()
        labels = {node.pref_label_he for node in nodes}
        assert "דת ושירותי דת" in labels
        assert "מינוי מועצה דתית" in labels
        assert "נושא כללי" not in labels

        links = session.execute(select(ArtifactSemanticLink)).scalars().all()
        linked_artifact_ids = {link.artifact_id for link in links}
        assert linked_artifact_ids == {"pf1_clean", "pf1_religion"}

        religious_node = next(node for node in nodes if node.pref_label_he == "מינוי מועצה דתית")
        religious_link = next(link for link in links if link.artifact_id == "pf1_religion")
        assert religious_link.semantic_node_id == religious_node.id


def _seed_pdf_first_topic_rows(session: Session) -> None:
    site = SourceSite(municipality_slug="ashdod", name="Ashdod", root_url="https://example.local")
    session.add(site)
    session.flush()
    document = Document(
        source_site_id=site.id,
        document_external_id="doc:bridge",
        canonical_url="https://example.local/bridge.pdf",
        title_he="ישיבת מועצה 2.2.2022",
        doc_kind="protocol_full",
        mime_hint="application/pdf",
        last_seen_at=datetime.utcnow(),
    )
    session.add(document)
    session.flush()
    version = DocumentVersion(
        document_id=document.id,
        sha256="8" * 64,
        byte_size=12,
        storage_uri="bridge.pdf",
        fetched_http_status=200,
        fetched_mime="application/pdf",
    )
    session.add(version)
    session.flush()
    extracted = ExtractedDocument(
        document_version_id=version.id,
        parser_name="stub",
        parser_version="test",
        status="completed",
        extracted_text="מינוי מועצה דתית\nהסכם בין עיריית אשדוד לבין חברה עירונית",
        page_count=1,
        pages_json=json.dumps([]),
        citation_map_json=json.dumps([]),
        quality_score=1.0,
        quality_flags_json=json.dumps([]),
        quality_summary_json=json.dumps({}),
        updated_at=datetime.utcnow(),
    )
    session.add(extracted)
    session.flush()

    rows = [
        (
            "pf1_clean",
            "הסכמים והתקשרויות",
            "הסכם בין עיריית אשדוד לבין החברה העירונית לתיירות אשדוד",
            "הסכם בין עיריית אשדוד לבין החברה העירונית לתיירות אשדוד מחליטים לאשר.",
        ),
        (
            "pf1_religion",
            "דת ושירותי דת",
            "שאילתא בנושא מינוי מועצה דתית",
            "שאילתא של ד\"ר לחמני בנושא מינוי מועצה דתית.",
        ),
        (
            "pf1_noise",
            "א לפקודת העיריות לאיחוד וחלוקה ושינויים במקרקעין בבעלות הרשות המקומית מחליטים פה אחד",
            "continuation",
            "א לפקודת העיריות לאיחוד וחלוקה ושינויים במקרקעין בבעלות הרשות המקומית מחליטים פה אחד",
        ),
    ]
    for ordinal, (artifact_id, primary_topic, structural_topic, text) in enumerate(rows):
        session.add(
            RetrievalArtifact(
                artifact_id=artifact_id,
                document_id=document.id,
                document_version_id=version.id,
                extracted_document_id=extracted.id,
                section_id=f"section-{ordinal}",
                source_kind="pdf_first_protocol",
                artifact_kind="pdf_first_retrieval_chunk",
                ordinal=ordinal,
                title_he=primary_topic,
                committee_name=None,
                meeting_date=None,
                header_path_json=json.dumps([structural_topic], ensure_ascii=False),
                body_text=text,
                retrieval_text=text,
                retrieval_text_norm=normalize_for_search(text),
                start_offset=ordinal * 1000,
                end_offset=ordinal * 1000 + len(text),
                start_page=1,
                end_page=1,
                citation_label="p.1",
                trigram_count=0,
                metadata_json=json.dumps({}),
            )
        )
        session.add(
            ArtifactTopicAnnotation(
                artifact_id=artifact_id,
                structural_topic_he=structural_topic,
                structural_topic_norm=normalize_for_search(structural_topic),
                primary_topic_he=primary_topic,
                primary_topic_norm=normalize_for_search(primary_topic),
                secondary_topics_json=json.dumps([], ensure_ascii=False),
                section_summary=text,
                classifier_confidence=0.9,
                classifier_route="dictalm_pdf_first_step4_5",
                provider_name="ollama",
                model_name="dictalm",
            )
        )
    session.commit()
