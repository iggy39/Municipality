from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Generator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import httpx
from sqlalchemy import select

from municipality.db import build_engine, build_session_factory
from municipality.fetcher import AssetFetcher
from municipality.migrations import apply_all
from municipality.models import (
    Decision,
    DecisionCitation,
    DecisionDocumentLink,
    Document,
    Meeting,
    MeetingDocumentLink,
    PipelineRun,
    PipelineRunStep,
    SemanticAlias,
    SemanticCandidateReject,
    SemanticDocumentRun,
    SemanticMention,
    SemanticNode,
    ChunkSemanticLink,
    DecisionSemanticLink,
    SourceSite,
    TextChunk,
    Vote,
)
from municipality.pipeline import PipelineService
from municipality.processing import ProcessingService
from municipality.search import SearchService


def _default_html_fetcher(url: str) -> str:
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


engine = build_engine()
SessionLocal = build_session_factory(engine)
app = FastAPI(title="Municipality API")


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _default_fallback_metadata() -> dict:
    return {
        "fallback_used": False,
        "fallback_reason": None,
        "fallback_provider": None,
        "fallback_model": None,
        "fallback_invoked_at": None,
        "fallback_validation_status": None,
        "fallback_validation_reasons": [],
    }


def _fallback_metadata_from_json(metadata_json: str | None) -> dict:
    metadata = _default_fallback_metadata()
    if not metadata_json:
        return metadata
    try:
        payload = json.loads(metadata_json)
    except json.JSONDecodeError:
        return metadata
    if not isinstance(payload, dict):
        return metadata
    merged = dict(payload)
    for key, value in metadata.items():
        merged.setdefault(key, value)
    metadata = merged
    if not isinstance(metadata["fallback_validation_reasons"], list):
        metadata["fallback_validation_reasons"] = []
    if "api_model_reasons" in metadata and not isinstance(metadata["api_model_reasons"], list):
        metadata["api_model_reasons"] = []
    return metadata


def _loads_json(value: str | None) -> dict | list | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, (dict, list)):
        return payload
    return None


def _semantic_node_payload(node: SemanticNode, *, child_count: int = 0) -> dict:
    return {
        "id": node.id,
        "label": node.pref_label_he,
        "label_norm": node.pref_label_norm,
        "kind": node.node_kind,
        "semantic_type": node.semantic_type,
        "depth": node.depth,
        "parent_id": node.parent_node_id,
        "specificity_score": node.specificity_score,
        "confidence": node.confidence,
        "support_count": node.support_count,
        "status": node.status,
        "child_count": child_count,
    }


def _decision_payload(decision_id: int, db) -> dict | None:
    decision = db.execute(
        select(Decision).where(Decision.id == decision_id, Decision.is_public.is_(True))
    ).scalar_one_or_none()
    if decision is None:
        return None

    meeting = db.execute(select(Meeting).where(Meeting.id == decision.meeting_id)).scalar_one_or_none()
    municipality_slug = None
    if meeting is not None:
        site = db.execute(select(SourceSite).where(SourceSite.id == meeting.source_site_id)).scalar_one_or_none()
        municipality_slug = site.municipality_slug if site else None

    citations = db.execute(
        select(DecisionCitation, Document)
        .join(Document, Document.id == DecisionCitation.document_id)
        .where(DecisionCitation.decision_id == decision.id)
        .order_by(DecisionCitation.page_number.asc(), DecisionCitation.start_offset.asc())
    ).all()

    vote = db.execute(select(Vote).where(Vote.decision_id == decision.id)).scalar_one_or_none()
    linked_docs = db.execute(
        select(DecisionDocumentLink, Document)
        .join(Document, Document.id == DecisionDocumentLink.document_id)
        .where(DecisionDocumentLink.decision_id == decision.id)
        .order_by(Document.id.asc())
    ).all()

    return {
        "decision": {
            "id": decision.id,
            "meeting_id": decision.meeting_id,
            "meeting_external_id": meeting.meeting_external_id if meeting else None,
            "municipality": municipality_slug,
            "decision_number": decision.decision_number,
            "agenda_item": decision.agenda_item,
            "decision_text": decision.decision_text,
            "parser_confidence": decision.parser_confidence,
            "source_type": "protocol",
            "metadata": _fallback_metadata_from_json(decision.metadata_json),
        },
        "votes": [
            {
                "for_count": vote.for_count,
                "against_count": vote.against_count,
                "abstain_count": vote.abstain_count,
                "unanimous": vote.unanimous,
                "is_uncertain": vote.is_uncertain,
                "confidence": vote.confidence,
                "raw_text": vote.raw_text,
            }
            for vote in [vote]
            if vote is not None
        ],
        "citations": [
            {
                "id": citation.id,
                "document": {
                    "id": document.id,
                    "title": document.title_he,
                    "url": document.canonical_url,
                    "source_type": citation.source_type,
                },
                "page": citation.page_number,
                "start_offset": citation.start_offset,
                "end_offset": citation.end_offset,
                "anchor_label": citation.anchor_label,
                "anchor_text": citation.anchor_text,
            }
            for citation, document in citations
        ],
        "linked_documents": [
            {
                "id": document.id,
                "title": document.title_he,
                "url": document.canonical_url,
                "doc_kind": document.doc_kind,
                "source_type": link.source_type,
                "provenance": link.provenance,
            }
            for link, document in linked_docs
        ],
    }


@app.on_event("startup")
def startup() -> None:
    apply_all(engine, Path("migrations"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/crawl/run")
def crawl_run(muni: str, root_url: str, db=Depends(get_db)) -> dict[str, int]:
    service = PipelineService(
        session=db,
        html_fetcher=_default_html_fetcher,
        fetcher=AssetFetcher(),
        storage_root=Path("storage/raw"),
    )
    run_id = service.run_crawl(muni, root_url)
    return {"run_id": run_id}


@app.post("/process/run")
def process_run(doc_id: int | None = None, muni: str | None = None, db=Depends(get_db)) -> dict[str, int]:
    service = ProcessingService(
        session=db,
        storage_root=Path("storage/raw"),
    )
    run_id = service.run(doc_id=doc_id, municipality_slug=muni)
    return {"run_id": run_id}


@app.get("/search")
def search(
    q: str,
    muni: str | None = None,
    source_type: str | None = None,
    year: int | None = None,
    topic: str | None = None,
    semantic_node_id: int | None = None,
    semantic_label: str | None = None,
    semantic_mode: str = "boost",
    include_semantic_debug: bool = False,
    limit: int = 20,
    db=Depends(get_db),
) -> dict:
    service = SearchService(db)
    hits = service.search(
        query=q,
        municipality_slug=muni,
        source_type=source_type,
        year=year,
        topic=topic,
        semantic_node_id=semantic_node_id,
        semantic_label=semantic_label,
        semantic_mode=semantic_mode,
        limit=max(1, min(limit, 50)),
    )
    results = [
        {
            "chunk_id": hit.chunk_id,
            "score": hit.score,
            "snippet": hit.snippet,
            "citation": hit.citation,
            "source_type": hit.source_type,
            "document": {
                "id": hit.document_id,
                "title": hit.document_title,
                "url": hit.document_url,
            },
            "municipality": hit.municipality_slug,
            "meeting_external_id": hit.meeting_external_id,
            "start_page": hit.start_page,
            "end_page": hit.end_page,
            "semantic_match_count": hit.semantic_match_count,
            "semantic_boost": hit.semantic_boost,
        }
        for hit in hits
    ]

    if include_semantic_debug:
        for idx, hit in enumerate(hits):
            results[idx]["semantic_nodes"] = [
                {
                    "id": node.id,
                    "label": node.label,
                    "kind": node.kind,
                    "type": node.semantic_type,
                    "confidence": node.confidence,
                }
                for node in hit.semantic_nodes
            ]
            results[idx]["semantic_node_ids"] = list(hit.semantic_node_ids)

    return {
        "query": q,
        "count": len(hits),
        "results": results,
    }


@app.get("/semantic/tree")
def semantic_tree(
    muni: str | None = None,
    root_id: int | None = None,
    depth: int | None = None,
    kind: str | None = None,
    db=Depends(get_db),
) -> dict:
    stmt = select(SemanticNode)
    if muni:
        stmt = stmt.join(SourceSite, SourceSite.id == SemanticNode.source_site_id).where(
            SourceSite.municipality_slug == muni
        )
    if kind:
        stmt = stmt.where(SemanticNode.node_kind == kind)

    nodes = db.execute(
        stmt.order_by(SemanticNode.depth.asc(), SemanticNode.pref_label_norm.asc(), SemanticNode.id.asc())
    ).scalars().all()
    nodes_by_id = {node.id: node for node in nodes}
    if root_id is not None and root_id not in nodes_by_id:
        raise HTTPException(status_code=404, detail="semantic_root_not_found")

    children_by_parent: dict[int, list[int]] = {}
    for node in nodes:
        if node.parent_node_id is None:
            continue
        children_by_parent.setdefault(node.parent_node_id, []).append(node.id)

    included_ids: set[int]
    if root_id is not None:
        max_relative_depth = max(0, depth) if depth is not None else None
        included_ids = {root_id}
        frontier: list[tuple[int, int]] = [(root_id, 0)]
        while frontier:
            parent_id, relative_depth = frontier.pop(0)
            if max_relative_depth is not None and relative_depth >= max_relative_depth:
                continue
            for child_id in children_by_parent.get(parent_id, []):
                if child_id in included_ids:
                    continue
                included_ids.add(child_id)
                frontier.append((child_id, relative_depth + 1))
    else:
        included_ids = {node.id for node in nodes}
        if depth is not None:
            max_depth = max(0, depth)
            included_ids = {node.id for node in nodes if node.depth <= max_depth}

    items = []
    for node in nodes:
        if node.id not in included_ids:
            continue
        child_count = sum(1 for child_id in children_by_parent.get(node.id, []) if child_id in included_ids)
        items.append(_semantic_node_payload(node, child_count=child_count))

    return {
        "count": len(items),
        "items": items,
    }


@app.get("/semantic/node/{node_id}")
def semantic_node_detail(node_id: int, db=Depends(get_db)) -> dict:
    node = db.execute(select(SemanticNode).where(SemanticNode.id == node_id)).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="semantic_node_not_found")

    parent = None
    if node.parent_node_id is not None:
        parent = db.execute(select(SemanticNode).where(SemanticNode.id == node.parent_node_id)).scalar_one_or_none()

    aliases = db.execute(
        select(SemanticAlias)
        .where(SemanticAlias.semantic_node_id == node.id)
        .order_by(SemanticAlias.alias_kind.asc(), SemanticAlias.alias_label_norm.asc())
    ).scalars().all()
    children = db.execute(
        select(SemanticNode)
        .where(SemanticNode.parent_node_id == node.id)
        .order_by(SemanticNode.pref_label_norm.asc(), SemanticNode.id.asc())
    ).scalars().all()

    decision_rows = db.execute(
        select(DecisionSemanticLink, Decision, Document, Meeting)
        .join(Decision, Decision.id == DecisionSemanticLink.decision_id)
        .join(Document, Document.id == Decision.source_document_id)
        .join(Meeting, Meeting.id == Decision.meeting_id)
        .where(DecisionSemanticLink.semantic_node_id == node.id)
        .order_by(Decision.id.asc())
    ).all()

    chunk_rows = db.execute(
        select(ChunkSemanticLink, TextChunk, Document)
        .join(TextChunk, TextChunk.chunk_id == ChunkSemanticLink.chunk_id)
        .join(Document, Document.id == TextChunk.document_id)
        .where(ChunkSemanticLink.semantic_node_id == node.id)
        .order_by(TextChunk.document_id.asc(), TextChunk.chunk_index.asc())
    ).all()

    mention_rows = db.execute(
        select(SemanticMention, Document)
        .join(Document, Document.id == SemanticMention.document_id)
        .where(SemanticMention.semantic_node_id == node.id)
        .order_by(SemanticMention.id.desc())
        .limit(30)
    ).all()

    return {
        "node": _semantic_node_payload(node, child_count=len(children)),
        "parent": _semantic_node_payload(parent, child_count=0) if parent else None,
        "children": [_semantic_node_payload(child, child_count=0) for child in children],
        "aliases": [
            {
                "id": alias.id,
                "label": alias.alias_label_he,
                "label_norm": alias.alias_label_norm,
                "kind": alias.alias_kind,
                "confidence": alias.confidence,
            }
            for alias in aliases
        ],
        "linked_decisions": [
            {
                "decision_id": decision.id,
                "relation_role": link.relation_role,
                "confidence": link.confidence,
                "source_mention_id": link.source_mention_id,
                "decision_text": decision.decision_text,
                "is_public": bool(decision.is_public),
                "meeting_id": decision.meeting_id,
                "meeting_external_id": meeting.meeting_external_id,
                "document": {
                    "id": document.id,
                    "title": document.title_he,
                    "url": document.canonical_url,
                },
            }
            for link, decision, document, meeting in decision_rows
        ],
        "linked_chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "confidence": link.confidence,
                "source_mention_id": link.source_mention_id,
                "source_type": chunk.source_kind,
                "citation": chunk.citation_label,
                "start_page": chunk.start_page,
                "end_page": chunk.end_page,
                "document": {
                    "id": document.id,
                    "title": document.title_he,
                    "url": document.canonical_url,
                },
                "snippet": chunk.chunk_text[:240],
            }
            for link, chunk, document in chunk_rows
        ],
        "mentions": [
            {
                "id": mention.id,
                "document_id": mention.document_id,
                "document_version_id": mention.document_version_id,
                "start_offset": mention.start_offset,
                "end_offset": mention.end_offset,
                "start_page": mention.start_page,
                "end_page": mention.end_page,
                "mention_text": mention.mention_text,
                "mention_confidence": mention.mention_confidence,
                "evidence_hash": mention.evidence_hash,
                "citation": (
                    f"p.{mention.start_page}"
                    if mention.start_page is not None and mention.start_page == mention.end_page
                    else (
                        f"pp.{mention.start_page}-{mention.end_page}"
                        if mention.start_page is not None and mention.end_page is not None
                        else None
                    )
                ),
                "document": {
                    "id": document.id,
                    "title": document.title_he,
                    "url": document.canonical_url,
                },
            }
            for mention, document in mention_rows
        ],
    }


@app.get("/semantic/runs/{document_version_id}")
def semantic_runs(document_version_id: int, db=Depends(get_db)) -> dict:
    runs = db.execute(
        select(SemanticDocumentRun)
        .where(SemanticDocumentRun.document_version_id == document_version_id)
        .order_by(SemanticDocumentRun.started_at.desc(), SemanticDocumentRun.id.desc())
    ).scalars().all()
    if not runs:
        return {
            "document_version_id": document_version_id,
            "count": 0,
            "runs": [],
        }

    run_ids = [run.id for run in runs]
    reject_rows = db.execute(
        select(SemanticCandidateReject.semantic_document_run_id, SemanticCandidateReject.reason_code).where(
            SemanticCandidateReject.semantic_document_run_id.in_(run_ids)
        )
    ).all()
    reject_histogram_by_run: dict[int, dict[str, int]] = {}
    for run_id, reason_code in reject_rows:
        bucket = reject_histogram_by_run.setdefault(run_id, {})
        bucket[reason_code] = bucket.get(reason_code, 0) + 1

    runs_payload = []
    for run in runs:
        validation_payload = _loads_json(run.validation_report_json)
        validation_dict = validation_payload if isinstance(validation_payload, dict) else {}
        issues_payload_raw = validation_dict.get("issues")
        issues_payload: list = issues_payload_raw if isinstance(issues_payload_raw, list) else []

        canonical_payload = _loads_json(run.canonicalization_report_json)
        canonical_dict = canonical_payload if isinstance(canonical_payload, dict) else {}
        accepted_nodes_raw = canonical_dict.get("accepted_nodes")
        rejected_nodes_raw = canonical_dict.get("rejected_nodes")
        warnings_raw = canonical_dict.get("warnings")
        accepted_nodes: list = accepted_nodes_raw if isinstance(accepted_nodes_raw, list) else []
        rejected_nodes: list = rejected_nodes_raw if isinstance(rejected_nodes_raw, list) else []
        warnings: list = warnings_raw if isinstance(warnings_raw, list) else []

        runs_payload.append(
            {
                "run_id": run.id,
                "status": run.status,
                "api_call_count": run.api_call_count,
                "prompt_hash": run.prompt_hash,
                "model_provider": run.model_provider,
                "model_name": run.model_name,
                "request_tokens": run.request_tokens,
                "response_tokens": run.response_tokens,
                "error_code": run.error_code,
                "error_text": run.error_text,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "validation": {
                    "is_valid": bool(validation_dict.get("is_valid")),
                    "issue_count": len(issues_payload),
                    "issues": issues_payload,
                },
                "canonicalization": {
                    "accepted_nodes": len(accepted_nodes),
                    "rejected_nodes": len(rejected_nodes),
                    "warning_count": len(warnings),
                    "warnings": warnings,
                },
                "reject_reason_histogram": reject_histogram_by_run.get(run.id, {}),
            }
        )

    return {
        "document_version_id": document_version_id,
        "count": len(runs_payload),
        "runs": runs_payload,
    }


@app.get("/meeting/{meeting_id}")
def meeting_detail(meeting_id: int, db=Depends(get_db)) -> dict:
    meeting = db.execute(select(Meeting).where(Meeting.id == meeting_id)).scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=404, detail="meeting_not_found")

    site = db.execute(select(SourceSite).where(SourceSite.id == meeting.source_site_id)).scalar_one_or_none()
    decisions = db.execute(
        select(Decision)
        .where(Decision.meeting_id == meeting.id, Decision.is_public.is_(True))
        .order_by(Decision.id.asc())
    ).scalars().all()
    links = db.execute(
        select(MeetingDocumentLink, Document)
        .join(Document, Document.id == MeetingDocumentLink.document_id)
        .where(MeetingDocumentLink.meeting_id == meeting.id)
        .order_by(MeetingDocumentLink.is_primary.desc(), Document.id.asc())
    ).all()

    decisions_payload = []
    for decision in decisions:
        vote = db.execute(select(Vote).where(Vote.decision_id == decision.id)).scalar_one_or_none()
        citation_count = db.execute(
            select(DecisionCitation.id).where(DecisionCitation.decision_id == decision.id)
        ).scalars().all()
        decisions_payload.append(
            {
                "id": decision.id,
                "decision_number": decision.decision_number,
                "agenda_item": decision.agenda_item,
                "decision_text": decision.decision_text,
                "parser_confidence": decision.parser_confidence,
                "citation_count": len(citation_count),
                "source_type": "protocol",
                "vote": {
                    "for_count": vote.for_count,
                    "against_count": vote.against_count,
                    "abstain_count": vote.abstain_count,
                    "unanimous": vote.unanimous,
                    "is_uncertain": vote.is_uncertain,
                }
                if vote
                else None,
                "metadata": _fallback_metadata_from_json(decision.metadata_json),
            }
        )

    return {
        "meeting": {
            "id": meeting.id,
            "municipality": site.municipality_slug if site else None,
            "meeting_external_id": meeting.meeting_external_id,
            "title_he": meeting.title_he,
            "committee_name": meeting.committee_name,
            "meeting_kind": meeting.meeting_kind,
            "meeting_code": meeting.meeting_code,
            "meeting_date": meeting.meeting_date,
            "parse_confidence": meeting.parse_confidence,
            "summary": None,
        },
        "documents": [
            {
                "id": document.id,
                "title": document.title_he,
                "url": document.canonical_url,
                "doc_kind": document.doc_kind,
                "source_type": link.source_type,
                "provenance": link.provenance,
                "is_primary": bool(link.is_primary),
            }
            for link, document in links
        ],
        "decisions": decisions_payload,
    }


@app.get("/decision/{decision_id}")
def decision_detail(decision_id: int, db=Depends(get_db)) -> dict:
    payload = _decision_payload(decision_id, db)
    if payload is None:
        raise HTTPException(status_code=404, detail="decision_not_found")
    return payload


@app.get("/ui/decision/{decision_id}", response_class=HTMLResponse)
def decision_card_page(decision_id: int, db=Depends(get_db)) -> HTMLResponse:
    payload = _decision_payload(decision_id, db)
    if payload is None:
        raise HTTPException(status_code=404, detail="decision_not_found")

    decision = payload["decision"]
    metadata = decision["metadata"]
    votes = payload["votes"]
    citations = payload["citations"]
    linked_documents = payload["linked_documents"]

    fallback_badge = ""
    if metadata.get("fallback_used"):
        fallback_badge = "<span class='badge fallback'>Fallback Bytez</span>"

    vote_html = "<div class='muted'>לא זוהו נתוני הצבעה.</div>"
    if votes:
        vote = votes[0]
        if vote.get("unanimous"):
            vote_html = "<div class='vote'>אושר פה אחד</div>"
        elif vote.get("is_uncertain"):
            vote_html = f"<div class='vote uncertain'>זוהתה הצבעה לא ודאית: {html.escape(vote.get('raw_text') or '')}</div>"
        else:
            vote_html = (
                "<div class='vote'>"
                f"בעד: {vote.get('for_count')} | נגד: {vote.get('against_count')} | נמנע: {vote.get('abstain_count')}"
                "</div>"
            )

    citation_parts: list[str] = []
    for item in citations:
        anchor_label = item.get("anchor_label") or f"עמוד {item['page']}"
        citation_parts.append(
            "<li>"
            f"<a href='{html.escape(item['document']['url'])}#page={item['page']}' target='_blank' rel='noopener'>"
            f"{html.escape(anchor_label)}"
            "</a>"
            f" <span class='muted'>{html.escape(item['document']['title'])}</span>"
            "</li>"
        )
    citation_items = "".join(citation_parts)

    document_items = "".join(
        (
            "<li>"
            f"<a href='{html.escape(item['url'])}' target='_blank' rel='noopener'>{html.escape(item['title'])}</a>"
            f" <span class='muted'>[{html.escape(item['source_type'])} | {html.escape(item['provenance'])}]</span>"
            "</li>"
        )
        for item in linked_documents
    )

    html_page = f"""
<!doctype html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>כרטיס החלטה {decision['id']}</title>
  <style>
    :root {{
      --bg-a: #f3f8f6;
      --bg-b: #fdfaf2;
      --ink: #15343b;
      --muted: #53707a;
      --panel: rgba(255,255,255,0.82);
      --line: #d7e6df;
      --accent: #0f7b80;
      --warn: #b65b1d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Heebo", "Rubik", sans-serif;
      color: var(--ink);
      background: radial-gradient(1200px 500px at 100% -10%, #d8ece7 0%, transparent 70%),
                  radial-gradient(1000px 400px at -10% 110%, #f9ead0 0%, transparent 70%),
                  linear-gradient(130deg, var(--bg-a), var(--bg-b));
      min-height: 100vh;
      padding: 22px;
    }}
    .card {{
      max-width: 900px;
      margin: 0 auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 24px;
      box-shadow: 0 14px 36px rgba(21, 52, 59, 0.11);
      backdrop-filter: blur(2px);
    }}
    h1 {{ margin: 0 0 10px; font-size: 1.7rem; line-height: 1.35; }}
    h2 {{ margin: 18px 0 8px; font-size: 1.05rem; }}
    p {{ margin: 0; line-height: 1.8; }}
    ul {{ margin: 8px 0 0; padding-inline-start: 20px; }}
    li {{ margin: 6px 0; line-height: 1.5; }}
    .top {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }}
    .badge {{
      display: inline-block;
      border-radius: 999px;
      font-size: 0.82rem;
      padding: 5px 10px;
      border: 1px solid var(--line);
      background: #e9f2ee;
    }}
    .badge.fallback {{ border-color: #f1c8a2; background: #fff2e5; color: #8a430f; }}
    .muted {{ color: var(--muted); font-size: 0.9rem; }}
    .vote {{ margin-top: 8px; font-weight: 600; color: var(--accent); }}
    .vote.uncertain {{ color: var(--warn); }}
    .grid {{ display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); margin-top: 14px; }}
    .panel {{ border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px; background: #fff; }}
    a {{ color: #00696f; text-decoration: none; border-bottom: 1px dotted #86b7bb; }}
    a:hover {{ border-bottom-style: solid; }}
  </style>
</head>
<body>
  <article class="card">
    <div class="top">
      <span class="badge">החלטה #{decision['id']}</span>
      <span class="badge">פגישת מקור: {html.escape(str(decision.get('meeting_external_id') or '-'))}</span>
      {fallback_badge}
    </div>
    <h1>{html.escape(decision.get('decision_text') or '')}</h1>
    <p class="muted">סעיף: {html.escape(decision.get('agenda_item') or 'סעיף לא מזוהה')}</p>
    <h2>תוצאת הצבעה</h2>
    {vote_html}
    <section class="grid">
      <section class="panel">
        <h2>ציטוטים מאומתים</h2>
        <ul>{citation_items or '<li class="muted">לא נמצאו ציטוטים.</li>'}</ul>
      </section>
      <section class="panel">
        <h2>מסמכים קשורים</h2>
        <ul>{document_items or '<li class="muted">לא נמצאו מסמכים קשורים.</li>'}</ul>
      </section>
    </section>
    <p class="muted" style="margin-top: 12px;">סטטוס אימות fallback: {html.escape(str(metadata.get('fallback_validation_status')))}</p>
  </article>
</body>
</html>
"""
    return HTMLResponse(html_page)


@app.get("/runs/{run_id}")
def run_status(run_id: int, db=Depends(get_db)) -> dict:
    run = db.execute(select(PipelineRun).where(PipelineRun.id == run_id)).scalar_one_or_none()
    if not run:
        return {"error": "not_found", "run_id": run_id}
    steps = db.execute(select(PipelineRunStep).where(PipelineRunStep.run_id == run_id)).scalars().all()
    return {
        "run_id": run.id,
        "status": run.status,
        "run_type": run.run_type,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "steps": [
            {"id": step.id, "step": step.step_name, "status": step.status, "item_ref": step.item_ref, "detail": step.detail}
            for step in steps
        ],
    }
