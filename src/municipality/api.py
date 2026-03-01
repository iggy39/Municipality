from __future__ import annotations

from pathlib import Path
from typing import Generator

from fastapi import Depends, FastAPI
import httpx
from sqlalchemy import select

from municipality.db import build_engine, build_session_factory
from municipality.fetcher import AssetFetcher
from municipality.migrations import apply_all
from municipality.models import PipelineRun, PipelineRunStep
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
        limit=max(1, min(limit, 50)),
    )
    return {
        "query": q,
        "count": len(hits),
        "results": [
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
            }
            for hit in hits
        ],
    }


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
